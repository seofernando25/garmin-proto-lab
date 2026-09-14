from __future__ import annotations

import asyncio
import zlib

from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.file_client import FileTransferClient
from garmin_proto_lab.filetransfer import (
    DirectoryFileFlag,
    DirectoryFilter,
    build_compressed_file_data,
    build_file_data,
)
from garmin_proto_lab.frame import Acknowledgement, Frame, ResponseStatus
from garmin_proto_lab.link import ResponseRejected


class FakeFileLink:
    def __init__(self) -> None:
        self.client: FileTransferClient | None = None
        self.requests: list[tuple[int, bytes]] = []
        self.responses: list[tuple[int, ResponseStatus | int, bytes]] = []
        self.download_payloads: list[bytes] = []
        self.file_bytes = b""
        self.compressed = False
        self.cancel_first = False
        self._download_count = 0
        self.supported_payload = bytes([1, 128, 4, 10]) + b"FIT_TYPE_4"
        self.filter_payload = b"\x00"
        self.unsupported_types = False

    async def respond(self, frame: Frame, status=ResponseStatus.ACK, payload=b"") -> None:
        self.responses.append((frame.message_type, status, bytes(payload)))

    async def request(self, message_type: int, payload=b"", **kwargs):
        self.requests.append((message_type, bytes(payload)))
        if message_type == 5007:
            return Acknowledgement(5007, ResponseStatus.ACK, self.filter_payload, 0)
        if message_type == 5031:
            if self.unsupported_types:
                raise ResponseRejected(Acknowledgement(5031, ResponseStatus.UNKNOWN_OR_NOT_SUPPORTED, b"", 0))
            return Acknowledgement(5031, ResponseStatus.ACK, self.supported_payload, 0)
        if message_type == 5008:
            return Acknowledgement(5008, ResponseStatus.ACK, b"", 0)
        if message_type != 5002:
            raise AssertionError(f"unexpected request {message_type}")

        self._download_count += 1
        self.download_payloads.append(bytes(payload))
        size = len(self.file_bytes)
        assert self.client is not None

        async def deliver() -> None:
            await asyncio.sleep(0)
            if self.cancel_first and self._download_count == 1:
                await self.client.handle(Frame(5022, b"", 9))
                return
            if self.compressed and payload[-1] == 1:
                midpoint = max(1, size // 2)
                chunks = [self.file_bytes[:midpoint], self.file_bytes[midpoint:]]
                running = 0
                position = 0
                for counter, clear in enumerate(chunks):
                    if not clear:
                        continue
                    position += len(clear)
                    running = crc16_arc(clear, running)
                    body = build_compressed_file_data(
                        zlib.compress(clear),
                        packet_counter=counter,
                        decompressed_position=position,
                        running_crc=running,
                        end_of_stream=True,
                    )
                    await self.client.handle(Frame(5054, body, 10 + counter))
            else:
                offset = 0
                running = 0
                midpoint = max(1, size // 2)
                for clear in (self.file_bytes[:midpoint], self.file_bytes[midpoint:]):
                    if not clear:
                        continue
                    body, running = build_file_data(clear, offset, running)
                    await self.client.handle(Frame(5004, body, 10 + offset))
                    offset += len(clear)

        asyncio.create_task(deliver())
        return Acknowledgement(5002, ResponseStatus.ACK, b"\x00" + size.to_bytes(4, "little"), 0)


def test_standard_read_and_file_data_responses() -> None:
    async def run() -> None:
        link = FakeFileLink()
        link.file_bytes = b"abcdefghijklmnopqrstuvwxyz"
        client = FileTransferClient(link, transfer_timeout=0.5, not_ready_retry_delay=0)  # type: ignore[arg-type]
        link.client = client
        result = await client.read_file(7, compression=False)
        assert result.data == link.file_bytes
        assert result.compressed is False
        assert link.download_payloads[0][:2] == b"\x07\x00"
        assert link.download_payloads[0][-1] == 0
        file_responses = [payload for message_type, _, payload in link.responses if message_type == 5004]
        assert file_responses[-1] == bytes([0]) + len(link.file_bytes).to_bytes(4, "little")

    asyncio.run(run())


def test_compressed_read_and_uncompressed_cancel_fallback() -> None:
    async def compressed() -> None:
        link = FakeFileLink()
        link.file_bytes = b"compressed-directory-data-" * 20
        link.compressed = True
        client = FileTransferClient(link, transfer_timeout=0.5, not_ready_retry_delay=0)  # type: ignore[arg-type]
        link.client = client
        result = await client.read_file(0, compression=True)
        assert result.data == link.file_bytes
        assert result.compressed is True
        assert result.compressed_bytes is not None and result.compressed_bytes < len(result.data)
        assert all(payload[1] == 0 for message_type, _, payload in link.responses if message_type == 5054)

    async def fallback() -> None:
        link = FakeFileLink()
        link.file_bytes = b"fallback works"
        link.cancel_first = True
        client = FileTransferClient(link, transfer_timeout=0.5, not_ready_retry_delay=0)  # type: ignore[arg-type]
        link.client = client
        result = await client.read_file(2, compression=True, fallback_uncompressed=True)
        assert result.data == link.file_bytes
        assert result.compressed is False
        assert [body[-1] for body in link.download_payloads] == [1, 0]
        assert any(message_type == 5022 and status is ResponseStatus.ACK for message_type, status, _ in link.responses)

    asyncio.run(compressed())
    asyncio.run(fallback())


def test_directory_listing_supported_types_unknown_header_and_fallback() -> None:
    async def run() -> None:
        link = FakeFileLink()
        header = bytes(range(16))
        entry = (
            (77).to_bytes(2, "little")
            + bytes([128, 4, 0x12, 0x34, 0xA5, int(DirectoryFileFlag.READ | DirectoryFileFlag.ARCHIVE)])
            + (999).to_bytes(4, "little")
            + (123456).to_bytes(4, "little")
        )
        link.file_bytes = header + entry
        client = FileTransferClient(link, transfer_timeout=0.5, not_ready_retry_delay=0)  # type: ignore[arg-type]
        link.client = client
        listing = await client.list_directory(DirectoryFilter.DEFAULT)
        assert listing.filter_supported is True
        assert listing.directory.header16 == header
        assert listing.files[0].entry.file_index == 77
        assert listing.files[0].entry.reserved_6 == 0xA5
        assert listing.files[0].type_name == "FIT_TYPE_4"
        assert link.requests[0] == (5007, b"\x01")

        link.unsupported_types = True
        types = await client.get_supported_file_types()
        assert [(item.data_type, item.subtype) for item in types] == [(128, 4), (128, 36)]

        await client.archive_file(77)
        assert link.requests[-1] == (5008, b"M\x00\x10")

    asyncio.run(run())


def test_data_without_active_receiver_is_aborted_safely() -> None:
    async def run() -> None:
        link = FakeFileLink()
        client = FileTransferClient(link, transfer_timeout=0.1)  # type: ignore[arg-type]
        link.client = client
        assert await client.handle(Frame(5004, b"garbage", 1)) is True
        assert link.responses[-1][2] == b"\x02"
        assert await client.handle(Frame(5054, b"\x07garbage", 2)) is True
        assert link.responses[-1][2] == bytes([7, 1])

    asyncio.run(run())
