"""Data-driven message dispatch and request/ack correlation for GFDI."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .frame import Acknowledgement, Frame, FrameError, parse_ack
from .messages import MESSAGE_NAMES

Handler = Callable[[Frame], Any]


@dataclass(frozen=True, slots=True)
class UnknownMessage:
    frame: Frame
    known_name: str | None


@dataclass(frozen=True, slots=True)
class HandledMessage:
    frame: Frame
    result: Any


class MessageDispatcher:
    """Explicit handler registry that preserves unknown/unhandled frames."""

    def __init__(self) -> None:
        self._handlers: dict[int, Handler] = {}

    def register(self, message_type: int, handler: Handler) -> None:
        if message_type in self._handlers:
            raise ValueError(f"handler already registered for {message_type}")
        self._handlers[message_type] = handler

    def unregister(self, message_type: int) -> None:
        self._handlers.pop(message_type, None)

    def dispatch(self, frame: Frame) -> HandledMessage | UnknownMessage:
        handler = self._handlers.get(frame.message_type)
        if handler is None:
            return UnknownMessage(frame, MESSAGE_NAMES.get(frame.message_type))
        return HandledMessage(frame, handler(frame))


@dataclass(frozen=True, slots=True)
class PendingRequest:
    message_type: int
    transaction_id: int
    deadline: float


@dataclass(frozen=True, slots=True)
class MatchedResponse:
    request: PendingRequest
    acknowledgement: Acknowledgement


@dataclass(frozen=True, slots=True)
class UnmatchedResponse:
    acknowledgement: Acknowledgement
    reason: str


@dataclass(frozen=True, slots=True)
class ExpiredRequest:
    request: PendingRequest


class RequestTracker:
    """Five-bit transaction allocator and acknowledgement matcher.

    The tracker is clock-agnostic: callers pass monotonic time explicitly,
    making timeout/reconnect behavior deterministic in tests and in replay.
    """

    def __init__(self, timeout_seconds: float = 45.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        self._next_transaction = 0
        self._pending: dict[int, PendingRequest] = {}

    @property
    def pending(self) -> tuple[PendingRequest, ...]:
        return tuple(self._pending[k] for k in sorted(self._pending))

    def allocate(self, message_type: int, now: float) -> PendingRequest:
        # Find the next free 5-bit transaction ID without overwriting an
        # outstanding request.  At most 32 requests can exist concurrently.
        for _ in range(32):
            transaction_id = self._next_transaction
            self._next_transaction = (self._next_transaction + 1) & 0x1F
            if transaction_id not in self._pending:
                request = PendingRequest(message_type, transaction_id, now + self.timeout_seconds)
                self._pending[transaction_id] = request
                return request
        raise RuntimeError("all 32 GFDI transaction IDs are in use")

    def resolve(self, response_frame: Frame) -> MatchedResponse | UnmatchedResponse:
        ack = parse_ack(response_frame)
        if ack.transaction_id is None:
            return UnmatchedResponse(ack, "response has no transaction id")
        request = self._pending.get(ack.transaction_id)
        if request is None:
            return UnmatchedResponse(ack, "transaction id is not pending")
        if ack.request_type != request.message_type:
            return UnmatchedResponse(ack, "acknowledged message type does not match pending request")
        del self._pending[ack.transaction_id]
        return MatchedResponse(request, ack)

    def cancel(self, transaction_id: int) -> PendingRequest | None:
        """Remove one pending request without treating it as a response.

        Used when an outer async timeout/cancellation fires before a peer ACK.
        """
        return self._pending.pop(transaction_id, None)

    def expire(self, now: float) -> list[ExpiredRequest]:
        expired_ids = [tid for tid, request in self._pending.items() if now >= request.deadline]
        expired: list[ExpiredRequest] = []
        for tid in sorted(expired_ids):
            expired.append(ExpiredRequest(self._pending.pop(tid)))
        return expired

    def reset(self) -> tuple[PendingRequest, ...]:
        """Drop outstanding transactions on a transport/session reset."""
        old = self.pending
        self._pending.clear()
        return old
