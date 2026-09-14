"""Optional Bleak adapter for hardware experiments.

No Garmin protocol logic lives here.  The import is lazy so the core package has
zero runtime dependencies and can be tested without Bluetooth or Bleak.
"""
from __future__ import annotations

import inspect
import sys
from typing import Any
from uuid import UUID

from .transport import (
    BleBackend,
    DiscoveredDevice,
    GattCharacteristic,
    GattService,
    DisconnectCallback,
    NotificationCallback,
    TransportError,
)


class BleakUnavailable(TransportError):
    pass


def _load_bleak():
    try:
        from bleak import BleakClient, BleakScanner  # type: ignore
    except ImportError as exc:
        raise BleakUnavailable(
            "Bleak is not installed; install the optional 'hardware' extra before BLE experiments"
        ) from exc
    return BleakClient, BleakScanner


def _uuid(value: str | UUID) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


class BleakBackend(BleBackend):
    """Cross-platform BLE backend for the hardware test plan.

    Pairing and explicit ATT-MTU requests are not uniformly exposed by Bleak.
    Where a public backend method exists it is used; otherwise the adapter
    reports the OS-negotiated MTU and fails pairing explicitly instead of
    pretending success.
    """

    def __init__(self, *, assume_bonded: bool = False, bluez_agent: bool = True) -> None:
        self.assume_bonded = assume_bonded
        self.bluez_agent = bluez_agent
        self._client: Any = None
        self._paired_by_us = False
        self._write_without_response: dict[UUID, bool] = {}
        self._notify_wrappers: dict[UUID, Any] = {}
        self._disconnect_callback: DisconnectCallback | None = None

    @property
    def client(self):
        if self._client is None:
            raise TransportError("Bleak client is not connected")
        return self._client

    async def scan(self, timeout: float):
        _BleakClient, BleakScanner = _load_bleak()
        result = await BleakScanner.discover(timeout=timeout, return_adv=True)
        devices: list[DiscoveredDevice] = []
        if isinstance(result, dict):
            values = result.values()
        else:
            # Older Bleak releases may ignore return_adv and return BLEDevice objects.
            values = ((item, None) for item in result)
        for entry in values:
            if isinstance(entry, tuple) and len(entry) == 2:
                device, advertisement = entry
            else:
                device, advertisement = entry, None
            service_uuids = getattr(advertisement, "service_uuids", None) or []
            name = getattr(advertisement, "local_name", None) or getattr(device, "name", None)
            rssi = getattr(advertisement, "rssi", None)
            if rssi is None:
                rssi = getattr(device, "rssi", None)
            devices.append(
                DiscoveredDevice(
                    str(getattr(device, "address")),
                    name,
                    frozenset(_uuid(value) for value in service_uuids),
                    int(rssi) if rssi is not None else None,
                )
            )
        return devices

    def set_disconnect_callback(self, callback: DisconnectCallback) -> None:
        self._disconnect_callback = callback

    def _on_disconnected(self, _client: Any) -> None:
        callback = self._disconnect_callback
        if callback is None:
            return
        result = callback()
        if inspect.isawaitable(result):
            import asyncio

            asyncio.get_running_loop().create_task(result)

    async def _connect(self, device: DiscoveredDevice, *, pair: bool) -> None:
        BleakClient, _BleakScanner = _load_bleak()
        self._client = BleakClient(
            device.address,
            disconnected_callback=self._on_disconnected,
            pair=pair,
            timeout=60 if pair else 30,
        )

        async def run_connect():
            result = self._client.connect()
            if inspect.isawaitable(result):
                return await result
            return result

        try:
            if pair and sys.platform.startswith("linux") and self.bluez_agent:
                from .bluez_agent import BluezPairingAgent
                async with BluezPairingAgent():
                    connected = await run_connect()
            else:
                connected = await run_connect()
        except BaseException:
            client, self._client = self._client, None
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            raise
        if connected is False:
            self._client = None
            raise TransportError("Bleak connect returned false")
        if pair:
            self._paired_by_us = True

    async def connect(self, device: DiscoveredDevice) -> None:
        await self._connect(device, pair=False)

    async def connect_with_pairing(self, device: DiscoveredDevice) -> None:
        """Pair before GATT service discovery, matching BlueZ/Bleak semantics."""
        await self._connect(device, pair=True)

    async def disconnect(self) -> None:
        client, self._client = self._client, None
        self._write_without_response.clear()
        self._notify_wrappers.clear()
        self._paired_by_us = False
        if client is not None:
            await client.disconnect()

    async def is_bonded(self) -> bool:
        if self.assume_bonded or self._paired_by_us:
            return True
        if sys.platform.startswith("linux") and self._client is not None:
            backend = getattr(self._client, "_backend", None)
            device_path = getattr(backend, "_device_path", None)
            if device_path:
                try:
                    from bleak.backends.bluezdbus.manager import get_global_bluez_manager
                    manager = await get_global_bluez_manager()
                    return bool(manager.is_paired(device_path))
                except Exception:
                    return False
        return False

    async def create_bond(self) -> None:
        client = self.client
        pair = getattr(client, "pair", None)
        if pair is None:
            raise TransportError("this Bleak platform/backend does not expose pairing")

        async def run_pair():
            result = pair()
            if inspect.isawaitable(result):
                result = await result
            return result

        if sys.platform.startswith("linux") and self.bluez_agent:
            from .bluez_agent import BluezPairingAgent
            async with BluezPairingAgent():
                result = await run_pair()
        else:
            result = await run_pair()
        if result is False:
            raise TransportError("Bleak pairing returned false")
        self._paired_by_us = True

    async def remove_bond(self, device: DiscoveredDevice) -> None:
        """Remove the OS Bluetooth pairing record for a controlled fresh-pair test."""
        BleakClient, _BleakScanner = _load_bleak()
        if self._client is not None:
            await self.disconnect()
        client = BleakClient(device.address)
        unpair = getattr(client, "unpair", None)
        if unpair is None:
            raise TransportError("this Bleak platform/backend does not expose unpairing")
        result = unpair()
        if inspect.isawaitable(result):
            await result
        self._paired_by_us = False

    async def discover_services(self):
        client = self.client
        services = getattr(client, "services", None)
        if services is None:
            getter = getattr(client, "get_services", None)
            if getter is None:
                raise TransportError("Bleak client exposes no service collection")
            services = getter()
            if inspect.isawaitable(services):
                services = await services

        out: list[GattService] = []
        self._write_without_response.clear()
        for service in services:
            characteristics: list[GattCharacteristic] = []
            for characteristic in getattr(service, "characteristics", ()):
                properties = frozenset(str(value).lower() for value in getattr(characteristic, "properties", ()))
                uuid = _uuid(getattr(characteristic, "uuid"))
                characteristics.append(GattCharacteristic(uuid, properties))
                if "write-without-response" in properties and "write" not in properties:
                    self._write_without_response[uuid] = True
            out.append(GattService(_uuid(getattr(service, "uuid")), tuple(characteristics)))
        return out

    async def request_mtu(self, mtu: int) -> int:
        client = self.client
        request = getattr(client, "request_mtu", None)
        if request is not None:
            result = request(mtu)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, int):
                return result
        # Public Bleak API commonly exposes only the negotiated mtu_size.
        negotiated = getattr(client, "mtu_size", 23)
        try:
            return int(negotiated)
        except (TypeError, ValueError):
            return 23

    async def subscribe(self, characteristic_uuid: UUID, callback: NotificationCallback) -> None:
        async def deliver(data: bytes) -> None:
            result = callback(bytes(data))
            if inspect.isawaitable(result):
                await result

        def wrapper(_sender: Any, data: bytearray | bytes) -> None:
            # Bleak callbacks are synchronous on some backends. Schedule any
            # coroutine without blocking the backend callback thread/loop.
            import asyncio

            asyncio.get_running_loop().create_task(deliver(bytes(data)))

        self._notify_wrappers[characteristic_uuid] = wrapper
        await self.client.start_notify(str(characteristic_uuid), wrapper)

    async def write(self, characteristic_uuid: UUID, data: bytes) -> None:
        without_response = self._write_without_response.get(characteristic_uuid, False)
        await self.client.write_gatt_char(
            str(characteristic_uuid),
            bytes(data),
            response=not without_response,
        )
