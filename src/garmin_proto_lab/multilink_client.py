"""Async MultiLink control/data channel over the existing BLE backend.

This is intentionally a narrow implementation of the statically recovered
registration/control plane. It does not claim hardware compatibility until a
watch trace validates characteristic choice, client ID and reliable data flow.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Sequence
from uuid import UUID

from .mlr import MlrPacket
from .multilink import (
    COMMAND_CLOSE_ALL_RESPONSE,
    COMMAND_CLOSE_HANDLE_RESPONSE,
    COMMAND_REGISTER_RESPONSE,
    FILE_TRANSFER_PIPE_SERVICE_IDS,
    MULTILINK_SERVICE_UUID,
    REGISTRATION_SERVICE_ID,
    CloseAllRequest,
    CloseAllResponse,
    CloseHandleRequest,
    CloseHandleResponse,
    InvalidHandleNotification,
    RegisterServiceRequest,
    RegisterServiceResponse,
    RegisterStatus,
    candidate_characteristic_pairs,
    paired_characteristic,
    parse_control_message,
)
from .transport import BleBackend, GattService


class MultiLinkClientError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MultiLinkService:
    service_id: int
    handle: int
    reliable: bool
    revision: int


@dataclass(slots=True)
class MultiLinkClient:
    backend: BleBackend
    services: Sequence[GattService]
    connection_id: int
    max_write_length: int
    timeout: float = 10.0
    notify_uuid: UUID | None = None
    write_uuid: UUID | None = None
    registration: MultiLinkService | None = None
    invalid_handles: asyncio.Queue[int] = field(default_factory=asyncio.Queue)
    _pending: dict[tuple[int, int], asyncio.Future[object]] = field(default_factory=dict)
    _raw_queues: dict[int, asyncio.Queue[bytes]] = field(default_factory=dict)
    _subscribed: set[UUID] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not 0 < self.connection_id <= 0xFFFFFFFFFFFFFFFF:
            raise MultiLinkClientError("MultiLink connection/client ID must be non-zero uint64")
        if self.max_write_length < 3:
            raise MultiLinkClientError("MultiLink max write length must be at least three bytes")

    def _multilink_service(self) -> GattService:
        for service in self.services:
            if service.uuid == MULTILINK_SERVICE_UUID:
                return service
        raise MultiLinkClientError("watch did not expose the recovered MultiLink service")

    async def _select_pair(self, notify_uuid: UUID, write_uuid: UUID) -> None:
        self.notify_uuid = notify_uuid
        self.write_uuid = write_uuid
        if notify_uuid not in self._subscribed:
            await self.backend.subscribe(notify_uuid, self._on_notification)
            self._subscribed.add(notify_uuid)

    async def initialize(self) -> MultiLinkService:
        service = self._multilink_service()
        characteristic_uuids = frozenset(characteristic.uuid for characteristic in service.characteristics)
        pairs = candidate_characteristic_pairs(characteristic_uuids)
        if not pairs:
            raise MultiLinkClientError("MultiLink service has no 0x2810..0x2819 data characteristic")

        last_error: Exception | None = None
        for notify_uuid, write_uuid in pairs:
            try:
                await self._select_pair(notify_uuid, write_uuid)
                await self.close_all()
                response = await self.register_service(REGISTRATION_SERVICE_ID, request_reliable=False)
                if response.status is RegisterStatus.SUCCESS and response.handle is not None:
                    self.registration = MultiLinkService(
                        REGISTRATION_SERVICE_ID,
                        response.handle,
                        response.reliable,
                        response.revision,
                    )
                    return self.registration
                if response.status is RegisterStatus.ALREADY_IN_USE and response.alternate_characteristic is not None:
                    alternate = response.alternate_characteristic
                    if alternate not in characteristic_uuids:
                        raise MultiLinkClientError("device suggested an unavailable MultiLink characteristic")
                    paired = paired_characteristic(alternate)
                    await self._select_pair(
                        alternate,
                        paired if paired in characteristic_uuids else alternate,
                    )
                    await self.close_all()
                    retry = await self.register_service(REGISTRATION_SERVICE_ID, request_reliable=False)
                    if retry.status is RegisterStatus.SUCCESS and retry.handle is not None:
                        self.registration = MultiLinkService(
                            REGISTRATION_SERVICE_ID,
                            retry.handle,
                            retry.reliable,
                            retry.revision,
                        )
                        return self.registration
                last_error = MultiLinkClientError(f"registration service failed with status {int(response.status)}")
            except Exception as exc:
                last_error = exc
        raise MultiLinkClientError("unable to initialize MultiLink registration channel") from last_error

    async def _request_control(self, payload: bytes, expected_command: int, service_id: int):
        write_uuid = self.write_uuid
        if write_uuid is None:
            raise MultiLinkClientError("MultiLink characteristic pair is not selected")
        key = (expected_command, service_id)
        if key in self._pending:
            raise MultiLinkClientError(f"MultiLink command {key} is already pending")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._pending[key] = future
        try:
            await self.backend.write(write_uuid, payload)
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self._pending.pop(key, None)

    async def register_service(self, service_id: int, *, request_reliable: bool) -> RegisterServiceResponse:
        result = await self._request_control(
            RegisterServiceRequest(self.connection_id, service_id, request_reliable).encode(),
            COMMAND_REGISTER_RESPONSE,
            service_id,
        )
        if not isinstance(result, RegisterServiceResponse):
            raise MultiLinkClientError("unexpected response to MultiLink register request")
        if result.connection_id != self.connection_id or result.service_id != service_id:
            raise MultiLinkClientError("MultiLink register response identifiers do not match request")
        return result

    async def open_file_transfer_service(self) -> MultiLinkService:
        errors: list[str] = []
        for service_id in FILE_TRANSFER_PIPE_SERVICE_IDS:
            response = await self.register_service(service_id, request_reliable=True)
            if response.status is RegisterStatus.SUCCESS and response.handle is not None:
                if not response.reliable:
                    errors.append(f"0x{service_id:04x}: device returned unreliable handle")
                    try:
                        await self.close_handle(service_id, response.handle)
                    except Exception:
                        pass
                    continue
                return MultiLinkService(service_id, response.handle, True, response.revision)
            errors.append(f"0x{service_id:04x}: status {int(response.status)}")
        raise MultiLinkClientError("no FileAccess MultiLink service available: " + "; ".join(errors))

    async def close_handle(self, service_id: int, handle: int) -> CloseHandleResponse:
        result = await self._request_control(
            CloseHandleRequest(self.connection_id, service_id, handle).encode(),
            COMMAND_CLOSE_HANDLE_RESPONSE,
            service_id,
        )
        if not isinstance(result, CloseHandleResponse):
            raise MultiLinkClientError("unexpected response to MultiLink close-handle request")
        return result

    async def close_all(self) -> CloseAllResponse:
        result = await self._request_control(
            CloseAllRequest(self.connection_id).encode(),
            COMMAND_CLOSE_ALL_RESPONSE,
            0,
        )
        if not isinstance(result, CloseAllResponse):
            raise MultiLinkClientError("unexpected response to MultiLink close-all request")
        return result

    async def send_raw(self, raw_packet: bytes) -> None:
        write_uuid = self.write_uuid
        if write_uuid is None:
            raise MultiLinkClientError("MultiLink characteristic pair is not selected")
        raw = bytes(raw_packet)
        if not raw:
            raise MultiLinkClientError("cannot send empty MultiLink packet")
        if len(raw) > self.max_write_length:
            raise MultiLinkClientError(
                f"MultiLink packet length {len(raw)} exceeds write length {self.max_write_length}"
            )
        await self.backend.write(write_uuid, raw)

    async def recv_raw(self, handle: int, *, timeout: float | None = None) -> bytes:
        queue = self._raw_queues.setdefault(handle, asyncio.Queue())
        try:
            return await asyncio.wait_for(queue.get(), self.timeout if timeout is None else timeout)
        except asyncio.TimeoutError as exc:
            raise MultiLinkClientError(f"timed out waiting for MultiLink handle 0x{handle:02x}") from exc

    async def _on_notification(self, data: bytes) -> None:
        raw = bytes(data)
        if not raw:
            return
        if raw[0] == 0:
            try:
                message = parse_control_message(raw)
            except Exception:
                return
            if isinstance(message, InvalidHandleNotification):
                await self.invalid_handles.put(message.handle)
                return
            if isinstance(message, RegisterServiceResponse):
                key = (COMMAND_REGISTER_RESPONSE, message.service_id)
            elif isinstance(message, CloseHandleResponse):
                key = (COMMAND_CLOSE_HANDLE_RESPONSE, message.service_id)
            elif isinstance(message, CloseAllResponse):
                key = (COMMAND_CLOSE_ALL_RESPONSE, 0)
            else:
                return
            future = self._pending.get(key)
            if future is not None and not future.done():
                future.set_result(message)
            return

        try:
            handle = MlrPacket.parse(raw).handle if raw[0] & 0x80 else raw[0]
        except Exception:
            return
        await self._raw_queues.setdefault(handle, asyncio.Queue()).put(raw)
