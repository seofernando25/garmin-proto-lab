"""Independent codecs/state helpers for GFDI file-transfer messages.

Facts are documented as P-0400..P-0406 in ``spec/PROTOCOL.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
import zlib

from .crc import crc16_arc


class FileTransferError(ValueError):
    pass


class FileDataStatus(IntEnum):
    OK = 0
    RETRY_LAST = 1
    ABORT = 2
    CRC_MISMATCH = 3
    OFFSET_MISMATCH = 4


class DownloadStatus(IntEnum):
    OK = 0
    INDEX_NOT_FOUND = 1
    NOT_READABLE = 2
    NOT_READY = 3
    INVALID_REQUEST = 4
    CRC_MISMATCH = 5
    RANGE_EXCEEDS_FILE = 6


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    file_index: int
    compression: bool = True
    data_offset: int = 0
    request_flag: int = 1
    reserved_u16: int = 0
    reserved_u32: int = 0

    def encode(self) -> bytes:
        if not 0 <= self.file_index <= 0xFFFF:
            raise FileTransferError("file_index out of uint16 range")
        if not 0 <= self.data_offset <= 0xFFFFFFFF:
            raise FileTransferError("data_offset out of uint32 range")
        if not 0 <= self.request_flag <= 0xFF:
            raise FileTransferError("request_flag out of byte range")
        if not 0 <= self.reserved_u16 <= 0xFFFF:
            raise FileTransferError("reserved_u16 out of uint16 range")
        if not 0 <= self.reserved_u32 <= 0xFFFFFFFF:
            raise FileTransferError("reserved_u32 out of uint32 range")
        return (
            self.file_index.to_bytes(2, "little")
            + self.data_offset.to_bytes(4, "little")
            + bytes([self.request_flag])
            + self.reserved_u16.to_bytes(2, "little")
            + self.reserved_u32.to_bytes(4, "little")
            + bytes([int(self.compression)])
        )


@dataclass(frozen=True, slots=True)
class DownloadResponse:
    status: DownloadStatus | int
    file_size: int | None = None


def parse_download_response(payload: bytes) -> DownloadResponse:
    if not payload:
        raise FileTransferError("download response is empty")
    raw_status = payload[0]
    try:
        status: DownloadStatus | int = DownloadStatus(raw_status)
    except ValueError:
        status = raw_status
    if raw_status == DownloadStatus.OK:
        if len(payload) < 5:
            raise FileTransferError("successful download response is truncated")
        return DownloadResponse(status, int.from_bytes(payload[1:5], "little"))
    return DownloadResponse(status, None)


@dataclass(frozen=True, slots=True)
class FileDataChunk:
    marker: int
    running_crc: int
    offset: int
    data: bytes


def parse_file_data(payload: bytes) -> FileDataChunk:
    if len(payload) < 7:
        raise FileTransferError("File Data payload shorter than seven bytes")
    return FileDataChunk(
        marker=payload[0],
        running_crc=int.from_bytes(payload[1:3], "little"),
        offset=int.from_bytes(payload[3:7], "little"),
        data=bytes(payload[7:]),
    )


def build_file_data(data: bytes, offset: int, previous_crc: int = 0, marker: int = 0) -> tuple[bytes, int]:
    if not 0 <= marker <= 0xFF:
        raise FileTransferError("marker out of byte range")
    if not 0 <= offset <= 0xFFFFFFFF:
        raise FileTransferError("offset out of uint32 range")
    crc = crc16_arc(data, previous_crc)
    payload = bytes([marker]) + crc.to_bytes(2, "little") + offset.to_bytes(4, "little") + data
    return payload, crc


def build_file_data_response(status: FileDataStatus | int, current_offset: int) -> bytes:
    status_value = int(status)
    if not 0 <= status_value <= 0xFF:
        raise FileTransferError("status out of byte range")
    if not 0 <= current_offset <= 0xFFFFFFFF:
        raise FileTransferError("current_offset out of uint32 range")
    return bytes([status_value]) + current_offset.to_bytes(4, "little")


@dataclass(frozen=True, slots=True)
class ReceiveResult:
    status: FileDataStatus
    response: bytes
    accepted_data: bytes
    finished: bool


class StandardFileReceiver:
    """State model for ordinary 5004 chunks.

    It mirrors the statically observed offset/CRC behavior but does not perform
    I/O; callers persist ``accepted_data`` only when status is OK.
    """

    def __init__(self, final_size: int, max_consecutive_invalid: int = 3) -> None:
        if final_size < 0:
            raise ValueError("final_size cannot be negative")
        self.final_size = final_size
        self.max_consecutive_invalid = max_consecutive_invalid
        self.current_offset = 0
        self.previous_offset: int | None = None
        self.running_crc = 0
        self.invalid_count = 0

    def accept(self, payload: bytes) -> ReceiveResult:
        chunk = parse_file_data(payload)
        if chunk.offset != self.current_offset:
            if self.invalid_count >= self.max_consecutive_invalid:
                return ReceiveResult(
                    FileDataStatus.ABORT,
                    build_file_data_response(FileDataStatus.ABORT, self.current_offset),
                    b"",
                    self.current_offset >= self.final_size,
                )
            self.invalid_count += 1
            if self.previous_offset is not None and chunk.offset == self.previous_offset:
                status = FileDataStatus.OK
            else:
                status = FileDataStatus.OFFSET_MISMATCH
            return ReceiveResult(
                status,
                build_file_data_response(status, self.current_offset),
                b"",
                self.current_offset >= self.final_size,
            )

        new_crc = crc16_arc(chunk.data, self.running_crc)
        if new_crc != chunk.running_crc:
            return ReceiveResult(
                FileDataStatus.CRC_MISMATCH,
                build_file_data_response(FileDataStatus.CRC_MISMATCH, self.current_offset),
                b"",
                False,
            )

        if self.current_offset + len(chunk.data) > self.final_size:
            return ReceiveResult(
                FileDataStatus.ABORT,
                build_file_data_response(FileDataStatus.ABORT, self.current_offset),
                b"",
                False,
            )

        self.previous_offset = chunk.offset
        self.current_offset += len(chunk.data)
        self.running_crc = new_crc
        self.invalid_count = 0
        return ReceiveResult(
            FileDataStatus.OK,
            build_file_data_response(FileDataStatus.OK, self.current_offset),
            chunk.data,
            self.current_offset >= self.final_size,
        )


@dataclass(frozen=True, slots=True)
class CompressedFileDataChunk:
    packet_counter: int
    flags: int
    decompressed_position: int
    running_crc: int
    compressed_data: bytes

    @property
    def end_of_stream(self) -> bool:
        return bool(self.flags & 0x02)


def parse_compressed_file_data(payload: bytes) -> CompressedFileDataChunk:
    if len(payload) < 8:
        raise FileTransferError("Compressed File Data payload shorter than eight bytes")
    return CompressedFileDataChunk(
        packet_counter=payload[0],
        flags=payload[1],
        decompressed_position=int.from_bytes(payload[2:6], "little"),
        running_crc=int.from_bytes(payload[6:8], "little"),
        compressed_data=bytes(payload[8:]),
    )


def build_compressed_file_data(
    compressed_data: bytes,
    *,
    packet_counter: int,
    decompressed_position: int,
    running_crc: int,
    first_or_restart: bool = False,
    end_of_stream: bool = False,
) -> bytes:
    if not 0 <= packet_counter <= 0xFF:
        raise FileTransferError("packet_counter out of byte range")
    if not 0 <= decompressed_position <= 0xFFFFFFFF:
        raise FileTransferError("decompressed_position out of uint32 range")
    if not 0 <= running_crc <= 0xFFFF:
        raise FileTransferError("running_crc out of uint16 range")
    flags = int(first_or_restart) | (0x02 if end_of_stream else 0)
    return (
        bytes([packet_counter, flags])
        + decompressed_position.to_bytes(4, "little")
        + running_crc.to_bytes(2, "little")
        + compressed_data
    )


@dataclass(frozen=True, slots=True)
class SupportedFileType:
    data_type: int
    subtype: int
    name: str


def parse_supported_file_types(payload: bytes) -> list[SupportedFileType]:
    if not payload:
        raise FileTransferError("Supported File Types payload is empty")
    count = payload[0]
    pos = 1
    out: list[SupportedFileType] = []
    for _ in range(count):
        if pos + 3 > len(payload):
            raise FileTransferError("truncated file-type entry")
        data_type, subtype, name_len = payload[pos], payload[pos + 1], payload[pos + 2]
        pos += 3
        end = pos + name_len
        if end > len(payload):
            raise FileTransferError("truncated file-type name")
        out.append(SupportedFileType(data_type, subtype, payload[pos:end].decode("utf-8", errors="replace")))
        pos = end
    if pos != len(payload):
        raise FileTransferError("trailing bytes after Supported File Types entries")
    return out


class CompressedDataStatus(IntEnum):
    OK = 0
    ABORT = 1
    INCORRECT_CRC = 2
    DECOMPRESS_FAILURE = 3
    UNEXPECTED_COUNTER = 4


@dataclass(frozen=True, slots=True)
class CompressedReceiveResult:
    status: CompressedDataStatus
    response: bytes
    accepted_data: bytes
    finished: bool
    decompressed_size: int
    compressed_size: int


class CompressedFileReceiver:
    """Streaming receiver for 5054 compressed file chunks.

    Garmin uses zlib-wrapped DEFLATE streams. A packet can terminate a stream
    (flag bit 1 / value 0x02), after which a fresh inflater is used while the
    cumulative output offset and CRC continue across streams.
    """

    def __init__(self, final_size: int) -> None:
        if final_size < 0:
            raise ValueError("final_size cannot be negative")
        self.final_size = final_size
        self.expected_counter = 0
        self.decompressed_size = 0
        self.compressed_size = 0
        self.running_crc = 0
        self._inflater = zlib.decompressobj()

    @staticmethod
    def _response(counter: int, status: CompressedDataStatus, expected: int | None = None) -> bytes:
        out = bytearray([counter & 0xFF, int(status)])
        if expected is not None:
            out.append(expected & 0xFF)
        return bytes(out)

    def accept(self, payload: bytes) -> CompressedReceiveResult:
        if not payload:
            response = self._response(self.expected_counter, CompressedDataStatus.ABORT)
            return CompressedReceiveResult(
                CompressedDataStatus.ABORT, response, b"", False, self.decompressed_size, self.compressed_size
            )
        counter = payload[0]
        if len(payload) < 8:
            response = self._response(counter, CompressedDataStatus.ABORT)
            return CompressedReceiveResult(
                CompressedDataStatus.ABORT, response, b"", False, self.decompressed_size, self.compressed_size
            )
        if counter != self.expected_counter:
            response = self._response(counter, CompressedDataStatus.UNEXPECTED_COUNTER, self.expected_counter)
            return CompressedReceiveResult(
                CompressedDataStatus.UNEXPECTED_COUNTER,
                response,
                b"",
                False,
                self.decompressed_size,
                self.compressed_size,
            )

        self.expected_counter = (self.expected_counter + 1) & 0xFF
        chunk = parse_compressed_file_data(payload)
        compressed = chunk.compressed_data
        try:
            output = self._inflater.decompress(compressed)
            if chunk.end_of_stream:
                output += self._inflater.flush()
        except zlib.error:
            response = self._response(counter, CompressedDataStatus.DECOMPRESS_FAILURE)
            return CompressedReceiveResult(
                CompressedDataStatus.DECOMPRESS_FAILURE,
                response,
                b"",
                False,
                self.decompressed_size,
                self.compressed_size,
            )

        self.compressed_size += len(compressed)
        new_size = self.decompressed_size + len(output)
        new_crc = crc16_arc(output, self.running_crc)
        # Static implementation checks the running CRC exactly when cumulative
        # output reaches the checkpoint named in this packet. Preserve that
        # semantics rather than inventing meaning for values that do not align.
        if new_size == chunk.decompressed_position and new_crc != chunk.running_crc:
            response = self._response(counter, CompressedDataStatus.INCORRECT_CRC)
            return CompressedReceiveResult(
                CompressedDataStatus.INCORRECT_CRC,
                response,
                b"",
                False,
                self.decompressed_size,
                self.compressed_size,
            )
        if new_size > self.final_size:
            response = self._response(counter, CompressedDataStatus.ABORT)
            return CompressedReceiveResult(
                CompressedDataStatus.ABORT, response, b"", False, self.decompressed_size, self.compressed_size
            )

        self.decompressed_size = new_size
        self.running_crc = new_crc
        if chunk.end_of_stream:
            self._inflater = zlib.decompressobj()
        response = self._response(counter, CompressedDataStatus.OK)
        return CompressedReceiveResult(
            CompressedDataStatus.OK,
            response,
            output,
            self.decompressed_size >= self.final_size,
            self.decompressed_size,
            self.compressed_size,
        )


class DirectoryFilter(IntEnum):
    NO_FILTER = 0
    DEFAULT = 1
    PENDING_UPLOADS_ONLY = 3


class DirectoryFileFlag(IntFlag):
    READ = 0x80
    WRITE = 0x40
    ERASE = 0x20
    ARCHIVE = 0x10
    APPEND = 0x08
    CRYPTO = 0x04


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    file_index: int
    data_type: int
    identifier: bytes
    reserved_6: int
    flags_raw: int
    size: int
    timestamp: int

    @property
    def subtype(self) -> int:
        return self.identifier[0]

    @property
    def flags(self) -> DirectoryFileFlag:
        return DirectoryFileFlag(self.flags_raw)


@dataclass(frozen=True, slots=True)
class DirectoryFile:
    header16: bytes
    entries: tuple[DirectoryEntry, ...]


def parse_directory_file(data: bytes) -> DirectoryFile:
    """Parse file-index zero: opaque 16-byte header + fixed 16-byte entries."""
    if len(data) < 16:
        raise FileTransferError("directory file shorter than sixteen-byte header")
    remainder = len(data) - 16
    if remainder % 16:
        raise FileTransferError("directory file has trailing partial entry")
    entries: list[DirectoryEntry] = []
    for pos in range(16, len(data), 16):
        raw = data[pos : pos + 16]
        entries.append(
            DirectoryEntry(
                file_index=int.from_bytes(raw[0:2], "little"),
                data_type=raw[2],
                identifier=bytes(raw[3:6]),
                reserved_6=raw[6],
                flags_raw=raw[7],
                size=int.from_bytes(raw[8:12], "little"),
                timestamp=int.from_bytes(raw[12:16], "little"),
            )
        )
    return DirectoryFile(bytes(data[:16]), tuple(entries))


def build_apply_directory_filter(filter_value: DirectoryFilter | int) -> bytes:
    value = int(filter_value)
    if not 0 <= value <= 0xFF:
        raise FileTransferError("directory filter out of byte range")
    return bytes([value])


def parse_apply_directory_filter_response(payload: bytes) -> bool:
    if not payload:
        raise FileTransferError("Apply Directory Filter response is empty")
    return payload[0] == 0


def build_archive_request(file_index: int) -> bytes:
    if not 0 <= file_index <= 0xFFFF:
        raise FileTransferError("file_index out of uint16 range")
    return file_index.to_bytes(2, "little") + bytes([DirectoryFileFlag.ARCHIVE])


def legacy_supported_file_types() -> tuple[SupportedFileType, ...]:
    """Static fallback used when 5031 is explicitly unsupported."""
    return (
        SupportedFileType(128, 4, "FIT_TYPE_4"),
        SupportedFileType(128, 36, "FIT_TYPE_36"),
    )
