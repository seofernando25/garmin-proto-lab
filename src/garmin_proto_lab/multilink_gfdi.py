"""GFDI byte-stream transport carried through Garmin MultiLink.

Some watches expose GFDI as MultiLink service 1 instead of a dedicated GFDI
GATT characteristic pair. This adapter turns that service back into the same
ordered byte stream consumed by :class:`GfdiMessageLink`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .mlr import MlrError, ReliableMlrSession
from .multilink_client import MultiLinkClient, MultiLinkService

ByteCallback = Callable[[bytes], Awaitable[None] | None]


class MultiLinkGfdiError(RuntimeError):
    pass


@dataclass(slots=True)
class MultiLinkGfdiChannel:
    client: MultiLinkClient
    service: MultiLinkService
    callback: ByteCallback
    receive_poll_seconds: float = 0.02
    _session: ReliableMlrSession | None = field(init=False, default=None)
    _task: asyncio.Task[None] | None = field(init=False, default=None)
    _closed: bool = field(init=False, default=False)
    _send_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)
    _window_event: asyncio.Event = field(init=False, default_factory=asyncio.Event)

    def __post_init__(self) -> None:
        if self.service.reliable:
            self._session = ReliableMlrSession(self.service.handle, self.client.max_write_length)
        self._window_event.set()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._receive_loop(), name="garmin-gfdi-multilink")

    async def _deliver(self, data: bytes) -> None:
        if not data:
            return
        result = self.callback(bytes(data))
        if result is not None:
            await result

    async def _receive_loop(self) -> None:
        try:
            while not self._closed:
                session = self._session
                try:
                    raw = await self.client.recv_raw(
                        self.service.handle,
                        timeout=self.receive_poll_seconds if session is not None else self.client.timeout,
                    )
                except Exception as exc:
                    if self._closed:
                        return
                    if session is None:
                        raise
                    # A short receive poll timeout drives MLR ACK/retransmission
                    # timers; other errors must terminate the channel.
                    from .multilink_client import MultiLinkClientError
                    if not isinstance(exc, MultiLinkClientError) or "timed out waiting" not in str(exc):
                        raise
                    ack = session.poll_ack()
                    if ack is not None:
                        await self.client.send_raw(ack)
                    retransmit = session.retransmit_due()
                    for packet in retransmit:
                        await self.client.send_raw(packet)
                    continue

                if session is None:
                    if not raw or raw[0] != self.service.handle:
                        raise MultiLinkGfdiError("received non-reliable GFDI packet on the wrong handle")
                    await self._deliver(raw[1:])
                    continue

                received = session.receive(raw)
                if received.newly_acked:
                    self._window_event.set()
                if received.acknowledgement is not None:
                    await self.client.send_raw(received.acknowledgement)
                if received.data is not None:
                    await self._deliver(received.data)
                ack = session.poll_ack()
                if ack is not None:
                    await self.client.send_raw(ack)
                for packet in session.retransmit_due():
                    await self.client.send_raw(packet)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True
            self._window_event.set()
            raise

    async def send(self, data: bytes) -> None:
        raw = bytes(data)
        if not raw:
            return
        async with self._send_lock:
            if self._closed:
                raise MultiLinkGfdiError("MultiLink GFDI channel is closed")
            session = self._session
            if session is None:
                payload_size = self.client.max_write_length - 1
                if payload_size < 1:
                    raise MultiLinkGfdiError("MultiLink write length leaves no GFDI payload")
                for offset in range(0, len(raw), payload_size):
                    await self.client.send_raw(
                        bytes((self.service.handle,)) + raw[offset : offset + payload_size]
                    )
                return

            position = 0
            chunk_size = self.client.max_write_length - 2
            loop = asyncio.get_running_loop()
            deadline = loop.time() + max(self.client.timeout, 1.0)
            while position < len(raw):
                if self._closed:
                    raise MultiLinkGfdiError("MultiLink GFDI channel closed during send")
                slots = session.available_send_slots
                if slots <= 0:
                    self._window_event.clear()
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise MultiLinkGfdiError("timed out waiting for MLR send window")
                    try:
                        await asyncio.wait_for(self._window_event.wait(), min(remaining, 0.1))
                    except asyncio.TimeoutError:
                        for packet in session.retransmit_due():
                            await self.client.send_raw(packet)
                    continue
                take = min(len(raw) - position, slots * chunk_size)
                try:
                    packets = session.send_blob(raw[position : position + take])
                except MlrError as exc:
                    raise MultiLinkGfdiError(str(exc)) from exc
                for packet in packets:
                    await self.client.send_raw(packet)
                position += take

    async def close(self, *, close_service: bool = True) -> None:
        if self._closed and self._task is None:
            return
        self._closed = True
        session = self._session
        if session is not None:
            ack = session.force_ack()
            if ack is not None:
                try:
                    await self.client.send_raw(ack)
                except Exception:
                    pass
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if close_service:
            try:
                await self.client.close_handle(self.service.service_id, self.service.handle)
            except Exception:
                pass
