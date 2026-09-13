"""Async GFDI protobuf request/response layer for messages 5043/5044/5045."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .frame import Frame, ResponseStatus
from .link import GfdiMessageLink
from .protobuf_transport import (
    CancelProtobuf,
    ProtobufChunk,
    ProtobufChunkAck,
    ProtobufChunkStatus,
    ProtobufReassembler,
    chunk_protobuf,
)
from .smart_proto import SmartMessage, smart_has_known_extension

PROTOBUF_REQUEST = 5043
PROTOBUF_RESPONSE = 5044
PROTOBUF_CANCEL = 5045


class ProtobufLinkError(RuntimeError):
    pass


class ProtobufRequestTimeout(ProtobufLinkError):
    pass


RequestHandlerResult = bytes | bool | None
RequestHandler = Callable[[int, bytes], RequestHandlerResult | Awaitable[RequestHandlerResult]]


@dataclass(slots=True)
class ProtobufSmartLink:
    link: GfdiMessageLink
    gfdi_payload_limit: int | None = None
    timeout: float = 30.0
    request_handler: RequestHandler | None = None
    _next_request_id: int = 0
    _pending: dict[int, asyncio.Future[bytes]] = field(default_factory=dict)
    _reassembly: dict[tuple[int, int], ProtobufReassembler] = field(default_factory=dict)

    def set_payload_limit(self, value: int) -> None:
        if value <= 14:
            raise ProtobufLinkError("protobuf GFDI payload limit must exceed 14 bytes")
        self.gfdi_payload_limit = value

    def _payload_limit(self) -> int:
        if self.gfdi_payload_limit is None:
            raise ProtobufLinkError("protobuf GFDI payload limit is unknown before device information")
        if self.gfdi_payload_limit <= 14:
            raise ProtobufLinkError("protobuf GFDI payload limit must exceed 14 bytes")
        return self.gfdi_payload_limit

    def _allocate_request_id(self) -> int:
        for _ in range(0x10000):
            request_id = self._next_request_id
            self._next_request_id = (self._next_request_id + 1) & 0xFFFF
            if request_id not in self._pending and all(key[1] != request_id for key in self._reassembly):
                return request_id
        raise ProtobufLinkError("all protobuf request IDs are in use")

    async def _send_chunks(self, message_type: int, request_id: int, serialized: bytes) -> None:
        chunks = chunk_protobuf(serialized, request_id, self._payload_limit())
        if not chunks:
            raise ProtobufLinkError("GDI Smart protobuf payload cannot be empty")
        for chunk in chunks:
            ack = await self.link.request(message_type, chunk.encode(), timeout=self.timeout)
            parsed = ProtobufChunkAck.parse(ack.payload)
            if parsed.request_id != request_id or parsed.data_offset != chunk.data_offset:
                raise ProtobufLinkError("protobuf chunk acknowledgement does not match request/offset")
            status = int(parsed.status)
            if parsed.failed or status not in (
                int(ProtobufChunkStatus.NO_ERROR),
                int(ProtobufChunkStatus.DUPLICATE_PACKET),
            ):
                raise ProtobufLinkError(f"protobuf chunk rejected with status {status}")

    async def request(self, serialized_smart: bytes, *, timeout: float | None = None) -> bytes:
        # Validate our outbound Smart message before creating protocol state.
        SmartMessage.parse(serialized_smart)
        request_id = self._allocate_request_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send_chunks(PROTOBUF_REQUEST, request_id, serialized_smart)
            try:
                return await asyncio.wait_for(future, timeout if timeout is not None else self.timeout)
            except asyncio.TimeoutError as exc:
                raise ProtobufRequestTimeout(f"timed out waiting for protobuf response {request_id}") from exc
        except BaseException:
            if not future.done():
                future.cancel()
            self._pending.pop(request_id, None)
            # The recovered implementation sends 5045 after a failed/cancelled
            # request. Cancellation is best-effort and must not hide the cause.
            try:
                await self.link.request(PROTOBUF_CANCEL, CancelProtobuf(request_id).encode(), timeout=min(self.timeout, 5.0))
            except BaseException:
                pass
            raise
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, serialized_smart: bytes) -> int:
        SmartMessage.parse(serialized_smart)
        request_id = self._allocate_request_id()
        await self._send_chunks(PROTOBUF_REQUEST, request_id, serialized_smart)
        return request_id

    async def respond(self, request_id: int, serialized_smart: bytes) -> None:
        SmartMessage.parse(serialized_smart)
        await self._send_chunks(PROTOBUF_RESPONSE, request_id, serialized_smart)

    def cancel_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ProtobufLinkError(reason))
        self._pending.clear()
        self._reassembly.clear()

    async def handle(self, frame: Frame) -> bool:
        if frame.is_response or frame.message_type not in {PROTOBUF_REQUEST, PROTOBUF_RESPONSE, PROTOBUF_CANCEL}:
            return False
        if frame.message_type == PROTOBUF_CANCEL:
            await self._handle_cancel(frame)
            return True
        await self._handle_chunk(frame)
        return True

    async def _handle_cancel(self, frame: Frame) -> None:
        try:
            cancel = CancelProtobuf.parse(frame.payload)
        except Exception:
            await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            return
        future = self._pending.get(cancel.request_id)
        if future is not None and not future.done():
            future.set_exception(ProtobufLinkError("device cancelled protobuf response"))
        self._reassembly.pop((PROTOBUF_RESPONSE, cancel.request_id), None)
        await self.link.respond(frame, ResponseStatus.ACK, frame.payload)

    async def _handle_chunk(self, frame: Frame) -> None:
        try:
            chunk = ProtobufChunk.parse(frame.payload)
        except Exception:
            await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            return

        key = (frame.message_type, chunk.request_id)
        reassembler = self._reassembly.get(key)
        if reassembler is None:
            can_create = chunk.data_offset == 0 and (
                frame.message_type == PROTOBUF_REQUEST or chunk.request_id in self._pending
            )
            if not can_create:
                ack = ProtobufChunkAck(
                    chunk.request_id,
                    chunk.data_offset,
                    True,
                    ProtobufChunkStatus.UNKNOWN_REQUEST_ID,
                )
                await self.link.respond(frame, ResponseStatus.ACK, ack.encode())
                return
            reassembler = ProtobufReassembler(chunk.request_id, chunk.total_length)
            self._reassembly[key] = reassembler

        result = reassembler.accept(chunk)
        status = result.status
        complete = result.completed
        response_to_send: bytes | None = None

        if complete is not None and status is ProtobufChunkStatus.NO_ERROR:
            try:
                SmartMessage.parse(complete)
            except Exception:
                result = reassembler.semantic_failure(chunk.data_offset, ProtobufChunkStatus.PARSE_ERROR)
                status = result.status
            else:
                if frame.message_type == PROTOBUF_RESPONSE:
                    future = self._pending.get(chunk.request_id)
                    if future is None:
                        result = reassembler.semantic_failure(chunk.data_offset, ProtobufChunkStatus.UNKNOWN_PROTOBUF_MESSAGE)
                        status = result.status
                    elif not future.done():
                        future.set_result(complete)
                else:
                    handled = False
                    handler = self.request_handler
                    if handler is not None:
                        outcome = handler(chunk.request_id, complete)
                        if inspect.isawaitable(outcome):
                            outcome = await outcome
                        if isinstance(outcome, bytes):
                            handled = True
                            response_to_send = outcome
                        else:
                            handled = bool(outcome)
                    elif smart_has_known_extension(complete):
                        # Known envelope but no semantic listener is still
                        # unhandled, matching the static UNKNOWN_MESSAGE path.
                        handled = False
                    if not handled:
                        result = reassembler.semantic_failure(chunk.data_offset, ProtobufChunkStatus.UNKNOWN_PROTOBUF_MESSAGE)
                        status = result.status

        await self.link.respond(frame, ResponseStatus.ACK, result.ack.encode())

        if complete is not None or status not in (
            ProtobufChunkStatus.NO_ERROR,
            ProtobufChunkStatus.DUPLICATE_PACKET,
        ):
            self._reassembly.pop(key, None)

        if response_to_send is not None and status is ProtobufChunkStatus.NO_ERROR:
            await self.respond(chunk.request_id, response_to_send)
