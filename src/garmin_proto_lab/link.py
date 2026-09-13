"""Async GFDI message link built from transport, wire codec and dispatcher."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from .codec import DecodeProblem, DecodedPacket, GfdiWireCodec, SecureOutbound
from .dispatcher import (
    HandledMessage,
    MatchedResponse,
    MessageDispatcher,
    RequestTracker,
    UnknownMessage,
    UnmatchedResponse,
)
from .frame import Acknowledgement, Frame, FrameError, ResponseStatus, encode_ack
from .secure_session import SecurePacketDecoder
from .transport import BleTransport, DiscoveredDevice


class LinkError(RuntimeError):
    pass


class RequestTimeout(LinkError):
    pass


class ResponseRejected(LinkError):
    def __init__(self, acknowledgement: Acknowledgement) -> None:
        self.acknowledgement = acknowledgement
        super().__init__(f"request {acknowledgement.request_type} rejected with status {int(acknowledgement.status)}")


@dataclass(frozen=True, slots=True)
class LinkProblem:
    stage: str
    reason: str
    raw: bytes | None = None


IncomingCallback = Callable[[HandledMessage | UnknownMessage], Any]


@dataclass(slots=True)
class GfdiMessageLink:
    transport: BleTransport
    codec: GfdiWireCodec = field(default_factory=GfdiWireCodec)
    dispatcher: MessageDispatcher = field(default_factory=MessageDispatcher)
    tracker: RequestTracker = field(default_factory=RequestTracker)
    request_timeout: float = 45.0
    problems: list[LinkProblem] = field(default_factory=list)
    incoming: asyncio.Queue[HandledMessage | UnknownMessage] = field(default_factory=asyncio.Queue)
    _pending: dict[int, asyncio.Future[Acknowledgement]] = field(default_factory=dict)
    _callback: IncomingCallback | None = None

    def set_incoming_callback(self, callback: IncomingCallback | None) -> None:
        self._callback = callback

    def activate_secure_session(self, session_key: bytes, host_iv4: bytes, device_iv4: bytes) -> None:
        """Enable outer secure wrapping for subsequent packets.

        Call only after the plaintext GFDI acknowledgement carrying the 5111
        secure-session response has been sent.
        """
        if len(session_key) != 16 or len(host_iv4) != 4 or len(device_iv4) != 4:
            raise LinkError("secure session requires 16-byte key and four-byte directional IVs")
        self.codec.outbound_secure = SecureOutbound(bytes(session_key), bytes(host_iv4))
        self.codec.inbound_secure = SecurePacketDecoder(bytes(session_key), bytes(device_iv4))

    def deactivate_secure_session(self) -> None:
        self.codec.outbound_secure = None
        self.codec.inbound_secure = None

    async def connect(self, device: DiscoveredDevice) -> None:
        await self.transport.connect(device, self.feed_transport_bytes)

    async def disconnect(self) -> None:
        self.cancel_pending("transport disconnected")
        await self.transport.disconnect()

    def cancel_pending(self, reason: str) -> None:
        for transaction_id, future in list(self._pending.items()):
            self.tracker.cancel(transaction_id)
            if not future.done():
                future.set_exception(LinkError(reason))
        self._pending.clear()

    async def send(self, frame: Frame) -> None:
        await self.transport.send(self.codec.encode(frame))

    async def notify(self, message_type: int, payload: bytes = b"") -> None:
        await self.send(Frame(message_type, payload))

    async def respond(
        self,
        request: Frame,
        status: ResponseStatus | int = ResponseStatus.ACK,
        payload: bytes = b"",
    ) -> None:
        if request.is_response:
            raise LinkError("cannot respond to an acknowledgement frame")
        raw = encode_ack(request.message_type, status, payload, request.transaction_id)
        from .frame import decode_frame

        await self.send(decode_frame(raw))

    async def request(
        self,
        message_type: int,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> Acknowledgement:
        loop = asyncio.get_running_loop()
        pending = self.tracker.allocate(message_type, loop.time())
        future: asyncio.Future[Acknowledgement] = loop.create_future()
        self._pending[pending.transaction_id] = future
        try:
            await self.send(Frame(message_type, payload, pending.transaction_id))
            try:
                ack = await asyncio.wait_for(future, timeout if timeout is not None else self.request_timeout)
            except asyncio.TimeoutError as exc:
                self.tracker.cancel(pending.transaction_id)
                self._pending.pop(pending.transaction_id, None)
                raise RequestTimeout(f"timed out waiting for response to {message_type}") from exc
            if ack.status != ResponseStatus.ACK:
                raise ResponseRejected(ack)
            return ack
        except BaseException:
            self.tracker.cancel(pending.transaction_id)
            self._pending.pop(pending.transaction_id, None)
            raise

    async def feed_transport_bytes(self, data: bytes) -> None:
        for result in self.codec.feed(data):
            if isinstance(result, DecodeProblem):
                self.problems.append(LinkProblem(result.stage, result.reason, result.decoded_packet))
                continue
            await self._handle_packet(result)

    async def _handle_packet(self, packet: DecodedPacket) -> None:
        frame = packet.frame
        if frame.is_response:
            try:
                resolved = self.tracker.resolve(frame)
            except FrameError as exc:
                self.problems.append(LinkProblem("ack", str(exc), packet.inner_frame_bytes))
                return
            if isinstance(resolved, UnmatchedResponse):
                self.problems.append(LinkProblem("ack", resolved.reason, packet.inner_frame_bytes))
                return
            if isinstance(resolved, MatchedResponse):
                tid = resolved.request.transaction_id
                future = self._pending.pop(tid, None)
                if future is None:
                    self.problems.append(LinkProblem("ack", "matched tracker request has no async waiter", packet.inner_frame_bytes))
                    return
                if not future.done():
                    future.set_result(resolved.acknowledgement)
                return

        dispatched = self.dispatcher.dispatch(frame)
        await self.incoming.put(dispatched)
        callback = self._callback
        if callback is not None:
            result = callback(dispatched)
            if hasattr(result, "__await__"):
                await result
