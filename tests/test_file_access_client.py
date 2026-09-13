from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from garmin_proto_lab.file_access_client import (
    FileAccessControlClient,
    FileAccessControlError,
    FileAccessMlrDownloader,
)
from garmin_proto_lab.file_access_proto import (
    DataTypeFormat,
    FileDataType,
    FileItemReference,
    ItemAccessResult,
    ItemListStatus,
    TransferStatusRequest,
    TransportProtocol,
    build_file_access_smart,
    build_service_message,
)
from garmin_proto_lab.mlr import MlrPacket
from garmin_proto_lab.multilink_client import MultiLinkService
from garmin_proto_lab.protobuf_wire import encode_message, encode_string, encode_uint, last_bytes, last_varint, parse_fields


class FakeProtobuf:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.requests: list[bytes] = []
        self.sent_responses: list[tuple[int, bytes]] = []

    async def request(self, smart: bytes) -> bytes:
        self.requests.append(smart)
        if not self.responses:
            raise AssertionError("unexpected protobuf request")
        return self.responses.pop(0)

    async def respond(self, request_id: int, smart: bytes) -> None:
        self.sent_responses.append((request_id, smart))


def _list_response(*, session: int | None, next_transaction: int | None, item: FileItemReference | None = None, status=0) -> bytes:
    body = bytearray(encode_uint(1, status))
    if session is not None:
        body += encode_uint(2, session)
    if next_transaction is not None:
        body += encode_uint(3, next_transaction)
    if item is not None:
        body += encode_message(4, item.encode())
    return build_file_access_smart(build_service_message(10, bytes(body)))


def _pull_response(*, result=0, transport=0, handle=1, compression=15) -> bytes:
    body = bytearray(encode_uint(1, result))
    if transport is not None:
        body += encode_uint(2, transport)
    if handle is not None:
        body += encode_uint(3, handle)
    if compression is not None:
        body += encode_uint(7, compression)
    return build_file_access_smart(build_service_message(2, bytes(body)))


def test_item_list_paginates_with_session_only_after_first_request() -> None:
    async def run() -> None:
        uid1 = UUID(int=1)
        uid2 = UUID(int=2)
        item1 = FileItemReference(uid1, FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "FIT_TYPE_4", 42), 100)
        item2 = FileItemReference(uid2, FileDataType(DataTypeFormat.GDXML_DATA_TYPE, gdxml_string_key=42), 200)
        proto = FakeProtobuf([
            _list_response(session=55, next_transaction=None, item=item1),
            _list_response(session=None, next_transaction=99, item=item2),
        ])
        client = FileAccessControlClient(proto)  # type: ignore[arg-type]
        listing = await client.list_items(
            transaction_id=10,
            included_data_types=(FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "activity"),),
            requested_modified_time=True,
        )
        assert [item.uid for item in listing.items] == [uid1, uid2]
        assert listing.next_transaction_id == 99
        assert listing.pages == 2
        assert listing.data_type_names == ("FIT_TYPE_4", "FIT_TYPE_4")
        assert [item.uid for item in listing.activity_health_items] == [uid1, uid2]

        first_service = last_bytes(parse_fields(proto.requests[0]), 43)
        first_body = last_bytes(parse_fields(first_service or b""), 9)
        first_fields = parse_fields(first_body or b"")
        assert last_varint(first_fields, 2) == 10
        assert any(field.number == 6 for field in first_fields)
        assert last_varint(first_fields, 7) == 0

        second_service = last_bytes(parse_fields(proto.requests[1]), 43)
        second_body = last_bytes(parse_fields(second_service or b""), 9)
        second_fields = parse_fields(second_body or b"")
        assert [(field.number, field.value) for field in second_fields] == [(1, 55)]
    asyncio.run(run())


def test_item_list_bounds_and_failure() -> None:
    async def run_bound() -> None:
        proto = FakeProtobuf([_list_response(session=1, next_transaction=None)] * 2)
        client = FileAccessControlClient(proto, max_pages=1)  # type: ignore[arg-type]
        with pytest.raises(FileAccessControlError, match="page bound"):
            await client.list_items()

    async def run_failure() -> None:
        proto = FakeProtobuf([_list_response(session=None, next_transaction=None, status=ItemListStatus.FAIL_OTHER)])
        client = FileAccessControlClient(proto)  # type: ignore[arg-type]
        with pytest.raises(FileAccessControlError, match="status 4"):
            await client.list_items()

    asyncio.run(run_bound())
    asyncio.run(run_failure())


def test_pull_negotiation_validates_transport_handle_and_compression() -> None:
    async def run() -> None:
        item = FileItemReference(UUID(int=7), None, 1024)
        proto = FakeProtobuf([_pull_response(handle=123, compression=15)])
        client = FileAccessControlClient(proto)  # type: ignore[arg-type]
        result = await client.begin_pull(item)
        assert result.transfer_handle == 123
        assert result.expected_size == 1024

        bad = FileAccessControlClient(FakeProtobuf([_pull_response(transport=9)]))  # type: ignore[arg-type]
        with pytest.raises(FileAccessControlError, match="MultiLink"):
            await bad.begin_pull(item)

        bad_window = FileAccessControlClient(FakeProtobuf([_pull_response(compression=8)]))  # type: ignore[arg-type]
        with pytest.raises(FileAccessControlError, match="compression window"):
            await bad_window.begin_pull(item)

        denied = FileAccessControlClient(FakeProtobuf([_pull_response(result=ItemAccessResult.UNKNOWN_ITEM, handle=None, compression=None)]))  # type: ignore[arg-type]
        with pytest.raises(FileAccessControlError, match="result 8"):
            await denied.begin_pull(item)
    asyncio.run(run())


def test_transfer_status_handler_delays_known_transfer_and_answers_unknown() -> None:
    async def run() -> None:
        proto = FakeProtobuf([])
        client = FileAccessControlClient(proto)  # type: ignore[arg-type]
        request = TransferStatusRequest(transfer_handle=55)
        smart = build_file_access_smart(build_service_message(5, request.encode()))

        immediate = await client.handle_incoming(1, smart)
        assert isinstance(immediate, bytes)
        service = last_bytes(parse_fields(immediate), 43)
        assert last_bytes(parse_fields(service or b""), 21) == b""

        client.track_transfer(55)
        assert await client.handle_incoming(2, smart) is True
        incoming = await client.wait_transfer_status(55, timeout=0.1)
        assert incoming.request_id == 2
        assert incoming.request.transfer_handle == 55
        await client.respond_transfer_status(incoming)
        assert proto.sent_responses and proto.sent_responses[0][0] == 2
        client.untrack_transfer(55)

    asyncio.run(run())


class FakeMultiLink:
    def __init__(self, control: FileAccessControlClient, transfer_handle: int, file_data: bytes) -> None:
        self.control = control
        self.transfer_handle = transfer_handle
        self.file_data = file_data
        self.max_write_length = 20
        self.sent: list[bytes] = []
        self.closed: list[tuple[int, int]] = []
        self._recv_count = 0

    async def open_file_transfer_service(self) -> MultiLinkService:
        return MultiLinkService(0x2018, 0x80, True, 1)

    async def send_raw(self, raw: bytes) -> None:
        self.sent.append(bytes(raw))

    async def recv_raw(self, handle: int, *, timeout: float | None = None) -> bytes:
        assert handle == 0x80
        self._recv_count += 1
        if self._recv_count == 1:
            # ACK our configure packet and return the 3-byte configure result.
            return MlrPacket(0x80, b"\x00\x00\x00", True, 0, 1).encode()
        if self._recv_count == 2:
            status = TransferStatusRequest(transfer_handle=self.transfer_handle)
            await self.control.handle_incoming(77, build_file_access_smart(build_service_message(5, status.encode())))
            return MlrPacket(0x80, self.file_data, True, 1, 1).encode()
        raise AssertionError("unexpected additional MultiLink receive")

    async def close_handle(self, service_id: int, handle: int):
        self.closed.append((service_id, handle))
        return object()


def test_uncompressed_file_access_mlr_download_end_to_end_offline() -> None:
    async def run() -> None:
        transfer_handle = 123
        file_data = b"FIT-DATA"
        item = FileItemReference(UUID(int=9), None, len(file_data))
        proto = FakeProtobuf([_pull_response(handle=transfer_handle, compression=None)])
        control = FileAccessControlClient(proto)  # type: ignore[arg-type]
        multilink = FakeMultiLink(control, transfer_handle, file_data)
        downloader = FileAccessMlrDownloader(control, multilink)  # type: ignore[arg-type]
        result = await downloader.download(item)
        assert result.data == file_data
        assert result.negotiation.transfer_handle == transfer_handle
        assert result.service.handle == 0x80
        assert proto.sent_responses and proto.sent_responses[0][0] == 77
        assert multilink.closed == [(0x2018, 0x80)]
        # First raw write is the reliable MLR-wrapped 10-byte pipe configure.
        first = MlrPacket.parse(multilink.sent[0])
        assert first.reliable is True
        assert first.handle == 0x80
        assert first.payload == b"\x00\x00" + transfer_handle.to_bytes(8, "little")

    asyncio.run(run())
