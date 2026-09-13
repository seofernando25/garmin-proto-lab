from __future__ import annotations

import asyncio

from garmin_proto_lab.frame import Acknowledgement, Frame, ResponseStatus
from garmin_proto_lab.protobuf_link import PROTOBUF_CANCEL, PROTOBUF_REQUEST, PROTOBUF_RESPONSE, ProtobufSmartLink
from garmin_proto_lab.protobuf_transport import ProtobufChunk, ProtobufChunkAck, ProtobufChunkStatus, chunk_protobuf
from garmin_proto_lab.protobuf_wire import encode_message
from garmin_proto_lab.smart_proto import SMART_CORE_EXTENSION, build_smart_extension


class FakeLink:
    def __init__(self) -> None:
        self.requests: list[tuple[int, bytes]] = []
        self.responses: list[tuple[Frame, ResponseStatus | int, bytes]] = []

    async def request(self, message_type: int, payload: bytes = b"", *, timeout=None):
        self.requests.append((message_type, payload))
        if message_type in (PROTOBUF_REQUEST, PROTOBUF_RESPONSE):
            chunk = ProtobufChunk.parse(payload)
            ack = ProtobufChunkAck(chunk.request_id, chunk.data_offset, False, ProtobufChunkStatus.NO_ERROR)
            return Acknowledgement(message_type, ResponseStatus.ACK, ack.encode(), 0)
        if message_type == PROTOBUF_CANCEL:
            return Acknowledgement(message_type, ResponseStatus.ACK, payload, 0)
        raise AssertionError(message_type)

    async def respond(self, frame: Frame, status=ResponseStatus.ACK, payload: bytes = b"") -> None:
        self.responses.append((frame, status, payload))


def _smart(payload: bytes = b"") -> bytes:
    return build_smart_extension(SMART_CORE_EXTENSION, encode_message(14, payload))


def test_notify_chunks_and_validates_ack() -> None:
    async def run() -> None:
        fake = FakeLink()
        proto = ProtobufSmartLink(fake, gfdi_payload_limit=24)  # type: ignore[arg-type]
        request_id = await proto.notify(_smart(b"0123456789abcdef"))
        sent = [(kind, ProtobufChunk.parse(payload)) for kind, payload in fake.requests]
        assert request_id == 0
        assert len(sent) >= 2
        assert all(kind == PROTOBUF_REQUEST for kind, _ in sent)
        assert b"".join(chunk.data for _, chunk in sent) == _smart(b"0123456789abcdef")
    asyncio.run(run())


def test_request_reassembles_5044_and_acks_each_chunk() -> None:
    async def run() -> None:
        fake = FakeLink()
        proto = ProtobufSmartLink(fake, gfdi_payload_limit=22, timeout=1)  # type: ignore[arg-type]
        task = asyncio.create_task(proto.request(_smart(b"request")))
        while not proto._pending:
            await asyncio.sleep(0)
        request_id = next(iter(proto._pending))
        response = _smart(b"response-that-spans-packets")
        for chunk in chunk_protobuf(response, request_id, 22):
            assert await proto.handle(Frame(PROTOBUF_RESPONSE, chunk.encode(), transaction_id=3))
        assert await task == response
        assert len(fake.responses) == len(chunk_protobuf(response, request_id, 22))
        for _, status, ack_bytes in fake.responses:
            assert status == ResponseStatus.ACK
            ack = ProtobufChunkAck.parse(ack_bytes)
            assert ack.status is ProtobufChunkStatus.NO_ERROR
            assert not ack.failed
    asyncio.run(run())


def test_incoming_unknown_request_reports_unknown_message() -> None:
    async def run() -> None:
        fake = FakeLink()
        proto = ProtobufSmartLink(fake, gfdi_payload_limit=64)  # type: ignore[arg-type]
        unknown_smart = build_smart_extension(77, b"abc")
        chunk = chunk_protobuf(unknown_smart, 9, 64)[0]
        await proto.handle(Frame(PROTOBUF_REQUEST, chunk.encode(), transaction_id=1))
        ack = ProtobufChunkAck.parse(fake.responses[-1][2])
        assert ack.failed is True
        assert ack.status is ProtobufChunkStatus.UNKNOWN_PROTOBUF_MESSAGE
    asyncio.run(run())


def test_incoming_handler_can_generate_5044_response() -> None:
    async def run() -> None:
        fake = FakeLink()
        response = _smart(b"reply")
        seen: list[tuple[int, bytes]] = []

        async def handler(request_id: int, smart: bytes):
            seen.append((request_id, smart))
            return response

        proto = ProtobufSmartLink(fake, gfdi_payload_limit=64, request_handler=handler)  # type: ignore[arg-type]
        request = _smart(b"incoming")
        chunk = chunk_protobuf(request, 44, 64)[0]
        await proto.handle(Frame(PROTOBUF_REQUEST, chunk.encode(), transaction_id=2))
        assert seen == [(44, request)]
        ack = ProtobufChunkAck.parse(fake.responses[-1][2])
        assert ack.status is ProtobufChunkStatus.NO_ERROR
        response_chunks = [ProtobufChunk.parse(payload) for kind, payload in fake.requests if kind == PROTOBUF_RESPONSE]
        assert response_chunks
        assert b"".join(item.data for item in response_chunks) == response
    asyncio.run(run())


def test_cancel_fails_pending_and_echoes_ack() -> None:
    async def run() -> None:
        fake = FakeLink()
        proto = ProtobufSmartLink(fake, gfdi_payload_limit=64)  # type: ignore[arg-type]
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        proto._pending[12] = future
        frame = Frame(PROTOBUF_CANCEL, (12).to_bytes(2, "little"), transaction_id=7)
        await proto.handle(frame)
        assert future.done()
        assert isinstance(future.exception(), Exception)
        assert fake.responses[-1][2] == frame.payload
    asyncio.run(run())
