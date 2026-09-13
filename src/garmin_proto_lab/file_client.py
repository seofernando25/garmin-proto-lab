"""High-level read-side GFDI file-transfer client.

The target-specific meaning of a file index is deliberately outside this
module. It can enumerate the directory (index zero), query supported type
labels, and read any advertised readable file while preserving unknown fields.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .filetransfer import (
    CompressedDataStatus,
    CompressedFileReceiver,
    DirectoryEntry,
    DirectoryFile,
    DirectoryFileFlag,
    DirectoryFilter,
    DownloadRequest,
    DownloadStatus,
    FileDataStatus,
    FileTransferError,
    StandardFileReceiver,
    SupportedFileType,
    build_apply_directory_filter,
    build_archive_request,
    legacy_supported_file_types,
    parse_apply_directory_filter_response,
    parse_directory_file,
    parse_download_response,
    parse_supported_file_types,
)
from .frame import Frame, ResponseStatus
from .link import GfdiMessageLink, ResponseRejected

MESSAGE_DOWNLOAD_FILE = 5002
MESSAGE_STANDARD_FILE_DATA = 5004
MESSAGE_APPLY_DIRECTORY_FILTER = 5007
MESSAGE_FILE_OPERATION = 5008
MESSAGE_CANCEL_TRANSFER = 5022
MESSAGE_SUPPORTED_FILE_TYPES = 5031
MESSAGE_COMPRESSED_FILE_DATA = 5054


class FileReadError(RuntimeError):
    pass


class FileTransferCancelled(FileReadError):
    pass


@dataclass(frozen=True, slots=True)
class FileReadResult:
    file_index: int
    data: bytes
    transferred_size: int
    elapsed_seconds: float
    compressed: bool
    compressed_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ListedFile:
    entry: DirectoryEntry
    type_name: str | None = None


@dataclass(frozen=True, slots=True)
class DirectoryListing:
    filter_supported: bool
    supported_types: tuple[SupportedFileType, ...]
    directory: DirectoryFile
    files: tuple[ListedFile, ...]

    @property
    def readable_unarchived(self) -> tuple[ListedFile, ...]:
        return tuple(
            item
            for item in self.files
            if item.entry.flags & DirectoryFileFlag.READ and not item.entry.flags & DirectoryFileFlag.ARCHIVE
        )


@dataclass(slots=True)
class _ReadSession:
    file_index: int
    requested_compression: bool
    completion: asyncio.Future[FileReadResult]
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    final_size: int | None = None
    receiver: StandardFileReceiver | CompressedFileReceiver | None = None
    message_type: int | None = None
    buffer: bytearray = field(default_factory=bytearray)
    started_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class FileTransferClient:
    link: GfdiMessageLink
    transfer_timeout: float = 60.0
    not_ready_retry_delay: float = 1.0
    _active: _ReadSession | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def active(self) -> bool:
        return self._active is not None

    async def handle(self, frame: Frame) -> bool:
        """Handle inbound 5004/5054/5022 transfer messages."""
        if frame.is_response or frame.message_type not in {
            MESSAGE_STANDARD_FILE_DATA,
            MESSAGE_COMPRESSED_FILE_DATA,
            MESSAGE_CANCEL_TRANSFER,
        }:
            return False

        session = self._active
        if frame.message_type == MESSAGE_CANCEL_TRANSFER:
            await self.link.respond(frame, ResponseStatus.ACK, b"")
            if session is not None and not session.completion.done():
                session.completion.set_exception(FileTransferCancelled("remote device cancelled file transfer"))
            return True

        if session is None:
            # Mirrors the static no-receiver fail-closed responses.
            if frame.message_type == MESSAGE_STANDARD_FILE_DATA:
                response = bytes([FileDataStatus.ABORT])
            else:
                counter = frame.payload[0] if frame.payload else 0
                response = bytes([counter, CompressedDataStatus.ABORT])
            await self.link.respond(frame, ResponseStatus.ACK, response)
            return True

        try:
            await asyncio.wait_for(session.ready.wait(), self.transfer_timeout)
        except asyncio.TimeoutError:
            await self.link.respond(frame, ResponseStatus.ACK, b"\x02" if frame.message_type == 5004 else b"\x00\x01")
            if not session.completion.done():
                session.completion.set_exception(FileReadError("file-data arrived before download request completed"))
            return True

        if session.final_size is None:
            raise FileReadError("internal transfer session has no final size")
        if session.message_type is None:
            session.message_type = frame.message_type
            if frame.message_type == MESSAGE_STANDARD_FILE_DATA:
                session.receiver = StandardFileReceiver(session.final_size)
            else:
                session.receiver = CompressedFileReceiver(session.final_size)
        elif session.message_type != frame.message_type:
            # The Garmin processor is selected by the first data message and is
            # not renegotiated mid-transfer. Treat switching message families as
            # malformed instead of feeding bytes to the wrong parser.
            response = b"\x02" if frame.message_type == MESSAGE_STANDARD_FILE_DATA else bytes([
                frame.payload[0] if frame.payload else 0,
                CompressedDataStatus.ABORT,
            ])
            await self.link.respond(frame, ResponseStatus.ACK, response)
            if not session.completion.done():
                session.completion.set_exception(FileReadError("file-data message family changed during transfer"))
            return True

        receiver = session.receiver
        assert receiver is not None
        try:
            result = receiver.accept(frame.payload)
        except FileTransferError as exc:
            await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            if not session.completion.done():
                session.completion.set_exception(FileReadError(str(exc)))
            return True

        await self.link.respond(frame, ResponseStatus.ACK, result.response)
        if result.accepted_data:
            session.buffer.extend(result.accepted_data)

        fatal = False
        if isinstance(receiver, StandardFileReceiver):
            fatal = result.status in (FileDataStatus.ABORT, FileDataStatus.CRC_MISMATCH)
            compressed_bytes = None
            compressed = False
        else:
            fatal = result.status in (
                CompressedDataStatus.ABORT,
                CompressedDataStatus.INCORRECT_CRC,
                CompressedDataStatus.DECOMPRESS_FAILURE,
            )
            compressed_bytes = result.compressed_size
            compressed = True

        if fatal:
            if not session.completion.done():
                session.completion.set_exception(FileReadError(f"file transfer failed with data status {int(result.status)}"))
        elif result.finished and not session.completion.done():
            session.completion.set_result(
                FileReadResult(
                    file_index=session.file_index,
                    data=bytes(session.buffer),
                    transferred_size=len(session.buffer),
                    elapsed_seconds=time.monotonic() - session.started_at,
                    compressed=compressed,
                    compressed_bytes=compressed_bytes,
                )
            )
        return True

    async def _start_download_request(self, file_index: int, compression: bool) -> int:
        request = DownloadRequest(file_index=file_index, compression=compression)
        attempts = 2
        for attempt in range(attempts):
            ack = await self.link.request(MESSAGE_DOWNLOAD_FILE, request.encode(), timeout=self.transfer_timeout)
            response = parse_download_response(ack.payload)
            if response.status == DownloadStatus.OK:
                assert response.file_size is not None
                return response.file_size
            if response.status == DownloadStatus.NOT_READY and attempt + 1 < attempts:
                await asyncio.sleep(self.not_ready_retry_delay)
                continue
            names = {
                DownloadStatus.INDEX_NOT_FOUND: "file index does not exist",
                DownloadStatus.NOT_READABLE: "file is not readable",
                DownloadStatus.NOT_READY: "device is not ready",
                DownloadStatus.INVALID_REQUEST: "invalid download request",
                DownloadStatus.CRC_MISMATCH: "download request CRC mismatch",
                DownloadStatus.RANGE_EXCEEDS_FILE: "requested range exceeds file size",
            }
            try:
                status = DownloadStatus(int(response.status))
                reason = names.get(status, f"download status {int(status)}")
            except ValueError:
                reason = f"unknown download status {int(response.status)}"
            raise FileReadError(reason)
        raise FileReadError("download request retry budget exhausted")

    async def _read_once(self, file_index: int, compression: bool) -> FileReadResult:
        loop = asyncio.get_running_loop()
        session = _ReadSession(file_index, compression, loop.create_future())
        self._active = session
        try:
            session.final_size = await self._start_download_request(file_index, compression)
            session.ready.set()
            if session.final_size == 0:
                return FileReadResult(file_index, b"", 0, time.monotonic() - session.started_at, compression, 0 if compression else None)
            try:
                return await asyncio.wait_for(session.completion, self.transfer_timeout)
            except asyncio.TimeoutError as exc:
                raise FileReadError("timed out waiting for file data") from exc
        finally:
            if self._active is session:
                self._active = None

    async def read_file(self, file_index: int, *, compression: bool = True, fallback_uncompressed: bool = True) -> FileReadResult:
        """Read one file, optionally retrying uncompressed after remote cancel."""
        async with self._lock:
            try:
                return await self._read_once(file_index, compression)
            except FileTransferCancelled:
                if not compression or not fallback_uncompressed:
                    raise
                return await self._read_once(file_index, False)

    async def get_supported_file_types(self) -> tuple[SupportedFileType, ...]:
        try:
            ack = await self.link.request(MESSAGE_SUPPORTED_FILE_TYPES, b"")
        except ResponseRejected as exc:
            if exc.acknowledgement.status == ResponseStatus.UNKNOWN_OR_NOT_SUPPORTED:
                return legacy_supported_file_types()
            raise
        return tuple(parse_supported_file_types(ack.payload))

    async def apply_directory_filter(self, filter_value: DirectoryFilter = DirectoryFilter.NO_FILTER) -> bool:
        ack = await self.link.request(MESSAGE_APPLY_DIRECTORY_FILTER, build_apply_directory_filter(filter_value))
        return parse_apply_directory_filter_response(ack.payload)

    async def list_directory(self, filter_value: DirectoryFilter = DirectoryFilter.NO_FILTER) -> DirectoryListing:
        filter_supported = await self.apply_directory_filter(filter_value)
        supported = await self.get_supported_file_types()
        directory_result = await self.read_file(0, compression=True)
        directory = parse_directory_file(directory_result.data)
        lookup = {(item.data_type, item.subtype): item.name for item in supported}
        files = tuple(
            ListedFile(entry, lookup.get((entry.data_type, entry.subtype)))
            for entry in directory.entries
        )
        return DirectoryListing(filter_supported, supported, directory, files)

    async def archive_file(self, file_index: int) -> None:
        await self.link.request(MESSAGE_FILE_OPERATION, build_archive_request(file_index))
