"""Generic BLE transport orchestration for the independent GFDI client.

The backend boundary is intentionally vendor-neutral. A platform adapter is
responsible for actual scanning/GATT I/O; this module owns only the connection
state machine, Garmin service selection, MTU-derived write sizing and ordered
notification delivery documented as P-0001..P-0011.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Protocol, Sequence
from uuid import UUID

from .ble_stream import split_att_writes
from .multilink import DEFAULT_INDEPENDENT_CLIENT_ID, MULTILINK_SERVICE_UUID
from .uuids import (
    CONNECT_MOBILE_READ,
    CONNECT_MOBILE_SERVICE,
    CONNECT_MOBILE_WRITE,
    GARMIN_SUFFIX,
    GENERIC_GFDI_READ,
    GENERIC_GFDI_WRITE,
)

NotificationCallback = Callable[[bytes], Awaitable[None] | None]
DisconnectCallback = Callable[[], Awaitable[None] | None]


class TransportError(RuntimeError):
    pass


class TransportState(str, Enum):
    NOT_STARTED = "not_started"
    SCANNING = "scanning"
    CONNECTING_GATT = "connecting_gatt"
    WAITING_FOR_BOND = "waiting_for_bond"
    DISCOVERING_SERVICES = "discovering_services"
    AVAILABLE = "available"
    DISCONNECTING = "disconnecting"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    address: str
    name: str | None = None
    service_uuids: frozenset[UUID] = frozenset()
    rssi: int | None = None


@dataclass(frozen=True, slots=True)
class GattCharacteristic:
    uuid: UUID
    properties: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class GattService:
    uuid: UUID
    characteristics: tuple[GattCharacteristic, ...] = ()

    def has_characteristic(self, uuid: UUID) -> bool:
        return any(characteristic.uuid == uuid for characteristic in self.characteristics)


@dataclass(frozen=True, slots=True)
class GfdiLink:
    service_uuid: UUID
    write_uuid: UUID
    notify_uuid: UUID
    family: str


@dataclass(frozen=True, slots=True)
class TransportEvent:
    state: TransportState
    detail: str = ""


class BleBackend(Protocol):
    """Minimal platform BLE surface required by :class:`BleTransport`."""

    async def scan(self, timeout: float) -> Sequence[DiscoveredDevice]: ...
    async def connect(self, device: DiscoveredDevice) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_bonded(self) -> bool: ...
    async def create_bond(self) -> None: ...
    async def discover_services(self) -> Sequence[GattService]: ...
    async def request_mtu(self, mtu: int) -> int: ...
    async def subscribe(self, characteristic_uuid: UUID, callback: NotificationCallback) -> None: ...
    async def write(self, characteristic_uuid: UUID, data: bytes) -> None: ...


def is_garmin_service_uuid(value: UUID) -> bool:
    text = str(value).lower()
    return value == CONNECT_MOBILE_SERVICE or (text.startswith("6a4e") and text.endswith(GARMIN_SUFFIX))


def candidate_score(device: DiscoveredDevice) -> int:
    """Score statically recognizable Garmin BLE candidates without guessing identity."""
    if CONNECT_MOBILE_SERVICE in device.service_uuids:
        return 100
    if any(is_garmin_service_uuid(uuid) for uuid in device.service_uuids):
        return 50
    return 0


def select_candidates(devices: Sequence[DiscoveredDevice]) -> list[DiscoveredDevice]:
    candidates = [device for device in devices if candidate_score(device) > 0]
    return sorted(
        candidates,
        key=lambda device: (
            -candidate_score(device),
            -(device.rssi if device.rssi is not None else -10_000),
            device.address,
        ),
    )


def select_gfdi_link(services: Sequence[GattService]) -> GfdiLink:
    """Choose a usable GFDI characteristic pair from discovered services.

    Prefer the dedicated Connect Mobile service when both expected
    characteristics exist. Otherwise accept the generic GFDI read/write pair
    only when both occur within the same discovered service. This intentionally
    avoids guessing the generic service UUID before target-watch discovery.
    """
    for service in services:
        if (
            service.uuid == CONNECT_MOBILE_SERVICE
            and service.has_characteristic(CONNECT_MOBILE_WRITE)
            and service.has_characteristic(CONNECT_MOBILE_READ)
        ):
            return GfdiLink(service.uuid, CONNECT_MOBILE_WRITE, CONNECT_MOBILE_READ, "connect_mobile")

    for service in services:
        if service.has_characteristic(GENERIC_GFDI_WRITE) and service.has_characteristic(GENERIC_GFDI_READ):
            return GfdiLink(service.uuid, GENERIC_GFDI_WRITE, GENERIC_GFDI_READ, "generic_gfdi")

    raise TransportError("no discovered service contains a complete GFDI characteristic pair")


@dataclass(slots=True)
class BleTransport:
    backend: BleBackend
    require_bond: bool = False
    requested_mtu: int = 515
    connect_timeout: float = 20.0
    bond_timeout: float = 45.0
    bond_attempts: int = 2
    operation_timeout: float = 15.0
    state: TransportState = TransportState.NOT_STARTED
    device: DiscoveredDevice | None = None
    link: GfdiLink | None = None
    services: tuple[GattService, ...] = ()
    negotiated_mtu: int = 23
    multilink_connection_id: int = DEFAULT_INDEPENDENT_CLIENT_ID
    multilink_client: Any | None = None
    _multilink_gfdi: Any | None = None
    _notification_callback: NotificationCallback | None = None
    events: list[TransportEvent] = field(default_factory=list)

    @property
    def write_payload_size(self) -> int:
        return max(1, self.negotiated_mtu - 3)

    def _set_state(self, state: TransportState, detail: str = "") -> None:
        self.state = state
        self.events.append(TransportEvent(state, detail))

    async def discover(self, timeout: float = 8.0) -> list[DiscoveredDevice]:
        if self.state not in (TransportState.NOT_STARTED, TransportState.FINISHED, TransportState.FAILED):
            raise TransportError(f"cannot scan while transport is {self.state.value}")
        self._set_state(TransportState.SCANNING)
        try:
            devices = await asyncio.wait_for(self.backend.scan(timeout), timeout + 1.0)
        except Exception as exc:
            self._set_state(TransportState.FAILED, f"scan failed: {type(exc).__name__}")
            raise TransportError("BLE scan failed") from exc
        candidates = select_candidates(devices)
        self._set_state(TransportState.NOT_STARTED, f"{len(candidates)} Garmin candidate(s)")
        return candidates

    async def connect(self, device: DiscoveredDevice, callback: NotificationCallback) -> GfdiLink:
        if self.state not in (TransportState.NOT_STARTED, TransportState.FINISHED, TransportState.FAILED):
            raise TransportError(f"cannot connect while transport is {self.state.value}")
        self.device = device
        self._notification_callback = callback
        self._set_state(TransportState.CONNECTING_GATT, device.name or device.address)
        try:
            setter = getattr(self.backend, "set_disconnect_callback", None)
            if setter is not None:
                result = setter(self._backend_disconnected)
                if result is not None and hasattr(result, "__await__"):
                    await result
            paired_connector = getattr(self.backend, "connect_with_pairing", None)
            if self.require_bond and paired_connector is not None:
                if self.bond_attempts < 1:
                    raise TransportError("bond_attempts must be at least one")
                self._set_state(TransportState.WAITING_FOR_BOND)
                last_bond_error: BaseException | None = None
                for attempt in range(1, self.bond_attempts + 1):
                    try:
                        await asyncio.wait_for(
                            paired_connector(device),
                            self.connect_timeout + self.bond_timeout,
                        )
                        if await asyncio.wait_for(self.backend.is_bonded(), self.operation_timeout):
                            last_bond_error = None
                            break
                        last_bond_error = TransportError("bonding did not complete")
                    except BaseException as exc:
                        last_bond_error = exc
                    if attempt == self.bond_attempts:
                        assert last_bond_error is not None
                        raise TransportError(
                            f"bonding failed after {self.bond_attempts} attempt(s)"
                        ) from last_bond_error
            else:
                await asyncio.wait_for(self.backend.connect(device), self.connect_timeout)
                if self.require_bond and not await asyncio.wait_for(self.backend.is_bonded(), self.operation_timeout):
                    if self.bond_attempts < 1:
                        raise TransportError("bond_attempts must be at least one")
                    self._set_state(TransportState.WAITING_FOR_BOND)
                    last_bond_error = None
                    for attempt in range(1, self.bond_attempts + 1):
                        try:
                            await asyncio.wait_for(self.backend.create_bond(), self.bond_timeout)
                            if await asyncio.wait_for(self.backend.is_bonded(), self.operation_timeout):
                                last_bond_error = None
                                break
                            last_bond_error = TransportError("bonding did not complete")
                        except BaseException as exc:
                            last_bond_error = exc
                        if attempt == self.bond_attempts:
                            assert last_bond_error is not None
                            raise TransportError(
                                f"bonding failed after {self.bond_attempts} attempt(s)"
                            ) from last_bond_error

            self._set_state(TransportState.DISCOVERING_SERVICES)
            services = tuple(await asyncio.wait_for(self.backend.discover_services(), self.operation_timeout))
            self.services = services
            mtu = await asyncio.wait_for(self.backend.request_mtu(self.requested_mtu), self.operation_timeout)
            if mtu < 23:
                raise TransportError(f"invalid negotiated MTU {mtu}")
            self.negotiated_mtu = mtu

            try:
                self.link = select_gfdi_link(services)
            except TransportError as direct_error:
                if not any(service.uuid == MULTILINK_SERVICE_UUID for service in services):
                    raise direct_error
                from .multilink_client import MultiLinkClient
                from .multilink_gfdi import MultiLinkGfdiChannel

                multilink = MultiLinkClient(
                    self.backend,
                    services,
                    self.multilink_connection_id,
                    self.write_payload_size,
                    timeout=self.operation_timeout,
                )
                await asyncio.wait_for(multilink.initialize(), self.operation_timeout)
                logical = await asyncio.wait_for(multilink.open_gfdi_service(), self.operation_timeout)
                if multilink.write_uuid is None or multilink.notify_uuid is None:
                    raise TransportError("MultiLink GFDI characteristic pair was not selected")
                channel = MultiLinkGfdiChannel(multilink, logical, self._deliver_notification)
                await channel.start()
                self.multilink_client = multilink
                self._multilink_gfdi = channel
                self.link = GfdiLink(
                    MULTILINK_SERVICE_UUID,
                    multilink.write_uuid,
                    multilink.notify_uuid,
                    "multilink_gfdi_reliable" if logical.reliable else "multilink_gfdi",
                )
            else:
                await asyncio.wait_for(
                    self.backend.subscribe(self.link.notify_uuid, self._deliver_notification),
                    self.operation_timeout,
                )

            self._set_state(TransportState.AVAILABLE, self.link.family)
            return self.link
        except Exception as exc:
            self._set_state(TransportState.FAILED, str(exc))
            channel, self._multilink_gfdi = self._multilink_gfdi, None
            if channel is not None:
                try:
                    await channel.close(close_service=False)
                except Exception:
                    pass
            self.multilink_client = None
            try:
                await self.backend.disconnect()
            except Exception:
                pass
            if isinstance(exc, TransportError):
                raise
            raise TransportError("BLE connection setup failed") from exc

    async def _deliver_notification(self, data: bytes) -> None:
        callback = self._notification_callback
        if callback is None or self.state != TransportState.AVAILABLE:
            return
        result = callback(bytes(data))
        if result is not None:
            await result

    async def _backend_disconnected(self) -> None:
        if self.state in (
            TransportState.CONNECTING_GATT,
            TransportState.WAITING_FOR_BOND,
            TransportState.DISCONNECTING,
            TransportState.FINISHED,
            TransportState.NOT_STARTED,
        ):
            return
        channel, self._multilink_gfdi = self._multilink_gfdi, None
        if channel is not None:
            await channel.close(close_service=False)
        self.multilink_client = None
        self.link = None
        self.services = ()
        self.negotiated_mtu = 23
        self._set_state(TransportState.FAILED, "unexpected BLE disconnect")

    async def reconnect(
        self,
        *,
        attempts: int = 3,
        initial_delay: float = 0.25,
        backoff: float = 2.0,
        max_delay: float = 5.0,
    ) -> GfdiLink:
        """Reconnect the last device with a bounded retry/backoff policy."""
        if attempts < 1:
            raise TransportError("reconnect attempts must be at least one")
        device = self.device
        callback = self._notification_callback
        if device is None or callback is None:
            raise TransportError("no previous device/notification callback is available for reconnect")
        delay = max(0.0, initial_delay)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._set_state(TransportState.NOT_STARTED, f"reconnect attempt {attempt}/{attempts}")
                return await self.connect(device, callback)
            except Exception as exc:
                last_error = exc
                if attempt >= attempts:
                    break
                if delay:
                    await asyncio.sleep(delay)
                delay = min(max_delay, delay * backoff)
        raise TransportError(f"reconnect failed after {attempts} attempt(s)") from last_error

    async def send(self, data: bytes) -> None:
        if self.state != TransportState.AVAILABLE or self.link is None:
            raise TransportError("transport is not available")
        if self._multilink_gfdi is not None:
            await asyncio.wait_for(self._multilink_gfdi.send(data), self.operation_timeout)
            return
        for chunk in split_att_writes(data, self.write_payload_size):
            await asyncio.wait_for(self.backend.write(self.link.write_uuid, chunk), self.operation_timeout)

    async def disconnect(self) -> None:
        if self.state in (TransportState.NOT_STARTED, TransportState.FINISHED):
            self._set_state(TransportState.FINISHED)
            return
        self._set_state(TransportState.DISCONNECTING)
        channel, self._multilink_gfdi = self._multilink_gfdi, None
        try:
            if channel is not None:
                await channel.close()
            await asyncio.wait_for(self.backend.disconnect(), self.operation_timeout)
        finally:
            self.multilink_client = None
            self.link = None
            self.services = ()
            self.device = None
            self._notification_callback = None
            self.negotiated_mtu = 23
            self._set_state(TransportState.FINISHED)
