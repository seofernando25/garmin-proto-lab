from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from garmin_proto_lab.transport import (
    BleTransport,
    DiscoveredDevice,
    GattCharacteristic,
    GattService,
    TransportError,
    TransportState,
    select_candidates,
    select_gfdi_link,
)
from garmin_proto_lab.uuids import (
    CONNECT_MOBILE_READ,
    CONNECT_MOBILE_SERVICE,
    CONNECT_MOBILE_WRITE,
    GENERIC_GFDI_READ,
    GENERIC_GFDI_WRITE,
)


class FakeBackend:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.bonded = False
        self.notification_callback = None
        self.disconnect_callback = None
        self.writes: list[tuple[UUID, bytes]] = []
        self.devices = [
            DiscoveredDevice("AA:00", "other", frozenset(), -10),
            DiscoveredDevice("AA:01", "generic", frozenset({UUID("6a4e2500-667b-11e3-949a-0800200c9a66")}), -30),
            DiscoveredDevice("AA:02", "dedicated", frozenset({CONNECT_MOBILE_SERVICE}), -80),
        ]
        self.services = [
            GattService(
                CONNECT_MOBILE_SERVICE,
                (
                    GattCharacteristic(CONNECT_MOBILE_WRITE, frozenset({"write"})),
                    GattCharacteristic(CONNECT_MOBILE_READ, frozenset({"notify"})),
                ),
            )
        ]
        self.mtu = 100

    async def scan(self, timeout: float):
        self.calls.append(("scan", timeout))
        return self.devices

    def set_disconnect_callback(self, callback) -> None:
        self.disconnect_callback = callback

    async def connect(self, device: DiscoveredDevice) -> None:
        self.calls.append(("connect", device.address))

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def is_bonded(self) -> bool:
        self.calls.append("is_bonded")
        return self.bonded

    async def create_bond(self) -> None:
        self.calls.append("create_bond")
        self.bonded = True

    async def discover_services(self):
        self.calls.append("discover_services")
        return self.services

    async def request_mtu(self, mtu: int) -> int:
        self.calls.append(("request_mtu", mtu))
        return self.mtu

    async def subscribe(self, characteristic_uuid: UUID, callback) -> None:
        self.calls.append(("subscribe", characteristic_uuid))
        self.notification_callback = callback

    async def write(self, characteristic_uuid: UUID, data: bytes) -> None:
        self.calls.append(("write", characteristic_uuid, bytes(data)))
        self.writes.append((characteristic_uuid, bytes(data)))


def test_candidate_selection_prefers_dedicated_service_then_generic() -> None:
    backend = FakeBackend()
    candidates = select_candidates(backend.devices)
    assert [item.address for item in candidates] == ["AA:02", "AA:01"]


def test_link_selection_requires_complete_pair_and_prefers_dedicated() -> None:
    generic_service = GattService(
        UUID("6a4e2400-667b-11e3-949a-0800200c9a66"),
        (GattCharacteristic(GENERIC_GFDI_WRITE), GattCharacteristic(GENERIC_GFDI_READ)),
    )
    dedicated_service = GattService(
        CONNECT_MOBILE_SERVICE,
        (GattCharacteristic(CONNECT_MOBILE_WRITE), GattCharacteristic(CONNECT_MOBILE_READ)),
    )
    link = select_gfdi_link([generic_service, dedicated_service])
    assert link.family == "connect_mobile"
    assert link.write_uuid == CONNECT_MOBILE_WRITE

    link = select_gfdi_link([generic_service])
    assert link.family == "generic_gfdi"

    with pytest.raises(TransportError, match="complete GFDI"):
        select_gfdi_link([GattService(CONNECT_MOBILE_SERVICE, (GattCharacteristic(CONNECT_MOBILE_WRITE),))])


def test_unbonded_transport_path_for_probe_or_saved_auth() -> None:
    async def run() -> None:
        backend = FakeBackend()
        transport = BleTransport(backend)
        candidates = await transport.discover(timeout=0.1)
        assert transport.state is TransportState.NOT_STARTED
        assert candidates[0].address == "AA:02"

        received: list[bytes] = []
        await transport.connect(candidates[0], lambda data: received.append(data))
        assert transport.state is TransportState.AVAILABLE
        assert transport.negotiated_mtu == 100
        assert transport.write_payload_size == 97
        assert "create_bond" not in backend.calls
        assert "is_bonded" not in backend.calls
        assert ("request_mtu", 515) in backend.calls
        assert backend.notification_callback is not None

        await backend.notification_callback(b"one")
        await backend.notification_callback(b"two")
        assert received == [b"one", b"two"]

        payload = bytes(range(256))
        await transport.send(payload)
        assert b"".join(chunk for _, chunk in backend.writes) == payload
        assert [len(chunk) for _, chunk in backend.writes] == [97, 97, 62]
        assert all(uuid == CONNECT_MOBILE_WRITE for uuid, _ in backend.writes)

        await transport.disconnect()
        assert transport.state is TransportState.FINISHED
        assert transport.link is None
        assert transport.negotiated_mtu == 23

        states = [event.state for event in transport.events]
        assert TransportState.SCANNING in states
        assert TransportState.CONNECTING_GATT in states
        assert TransportState.WAITING_FOR_BOND not in states
        assert TransportState.DISCOVERING_SERVICES in states
        assert TransportState.AVAILABLE in states
        assert states[-2:] == [TransportState.DISCONNECTING, TransportState.FINISHED]

    asyncio.run(run())



def test_explicit_system_bond_path_remains_available() -> None:
    async def run() -> None:
        backend = FakeBackend()
        transport = BleTransport(backend, require_bond=True)
        assert transport.bond_timeout == 45.0
        await transport.connect(backend.devices[2], lambda _: None)
        assert "is_bonded" in backend.calls
        assert "create_bond" in backend.calls
        assert TransportState.WAITING_FOR_BOND in [event.state for event in transport.events]
        await transport.disconnect()

    asyncio.run(run())

def test_connection_failure_is_bounded_and_disconnects() -> None:
    class MissingServiceBackend(FakeBackend):
        async def discover_services(self):
            self.calls.append("discover_services")
            return []

    async def run() -> None:
        backend = MissingServiceBackend()
        transport = BleTransport(backend, require_bond=False, operation_timeout=0.1)
        with pytest.raises(TransportError, match="complete GFDI"):
            await transport.connect(backend.devices[2], lambda _: None)
        assert transport.state is TransportState.FAILED
        assert "disconnect" in backend.calls

    asyncio.run(run())


def test_transport_rejects_wrong_state_and_bad_mtu() -> None:
    async def run() -> None:
        backend = FakeBackend()
        transport = BleTransport(backend)
        with pytest.raises(TransportError, match="not available"):
            await transport.send(b"x")

        backend.mtu = 10
        with pytest.raises(TransportError, match="invalid negotiated MTU"):
            await transport.connect(backend.devices[2], lambda _: None)
        assert transport.state is TransportState.FAILED

    asyncio.run(run())


def test_unexpected_disconnect_and_bounded_reconnect() -> None:
    class FlakyReconnectBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = 0

        async def connect(self, device: DiscoveredDevice) -> None:
            await super().connect(device)
            if self.fail_next:
                self.fail_next -= 1
                raise RuntimeError("simulated reconnect failure")

    async def run() -> None:
        backend = FlakyReconnectBackend()
        backend.bonded = True
        transport = BleTransport(backend, operation_timeout=0.1, connect_timeout=0.1)
        await transport.connect(backend.devices[2], lambda _: None)
        assert backend.disconnect_callback is not None
        await backend.disconnect_callback()
        assert transport.state is TransportState.FAILED
        assert transport.device is not None

        backend.fail_next = 2
        link = await transport.reconnect(attempts=3, initial_delay=0)
        assert link.family == "connect_mobile"
        assert transport.state is TransportState.AVAILABLE
        reconnect_events = [event.detail for event in transport.events if "reconnect attempt" in event.detail]
        assert reconnect_events == ["reconnect attempt 1/3", "reconnect attempt 2/3", "reconnect attempt 3/3"]

    asyncio.run(run())


def test_system_bond_retries_once_like_pairing_strategy() -> None:
    class RetryBondBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.bond_calls = 0

        async def create_bond(self) -> None:
            self.calls.append("create_bond")
            self.bond_calls += 1
            if self.bond_calls == 1:
                raise RuntimeError("first bond attempt failed")
            self.bonded = True

    async def run() -> None:
        backend = RetryBondBackend()
        transport = BleTransport(backend, require_bond=True)
        await transport.connect(backend.devices[2], lambda _: None)
        assert backend.bond_calls == 2
        assert transport.state is TransportState.AVAILABLE
        await transport.disconnect()

    asyncio.run(run())


def test_multilink_gfdi_fallback_when_no_direct_gfdi_service() -> None:
    from garmin_proto_lab.mlr import MlrPacket
    from garmin_proto_lab.multilink import (
        MULTILINK_PAIRED_CHARACTERISTICS,
        MULTILINK_PRIMARY_CHARACTERISTICS,
        MULTILINK_SERVICE_UUID,
        REGISTRATION_SERVICE_ID,
    )

    class MultiLinkOnlyBackend(FakeBackend):
        def __init__(self) -> None:
            super().__init__()
            self.bonded = True
            self.services = [
                GattService(
                    MULTILINK_SERVICE_UUID,
                    (
                        GattCharacteristic(MULTILINK_PRIMARY_CHARACTERISTICS[0], frozenset({"notify"})),
                        GattCharacteristic(MULTILINK_PAIRED_CHARACTERISTICS[0], frozenset({"write-without-response"})),
                    ),
                )
            ]
            self.mtu = 100

        async def write(self, characteristic_uuid: UUID, data: bytes) -> None:
            await super().write(characteristic_uuid, data)
            raw = bytes(data)
            callback = self.notification_callback
            if callback is None or not raw:
                return
            if raw[0] == 1:
                if len(raw) >= 2 and raw[1] == 0:
                    await callback(b"\x01\x00\x02")
                return
            if raw[0] != 0:
                return
            command = raw[1]
            connection = raw[2:10]
            if command == 5:
                await callback(b"\x00\x06" + connection + b"\x00\x00\x00")
            elif command == 0:
                service = raw[10:12]
                service_id = int.from_bytes(service, "little")
                if service_id == REGISTRATION_SERVICE_ID:
                    handle, flags = 1, 0
                else:
                    handle, flags = 0x80, 1
                await callback(
                    b"\x00\x01" + connection + service + b"\x00" + bytes((handle, flags, 1))
                )
            elif command == 2:
                service = raw[10:12]
                await callback(b"\x00\x03" + connection + service + bytes((raw[12], 0)))

    async def run() -> None:
        backend = MultiLinkOnlyBackend()
        transport = BleTransport(backend, operation_timeout=0.5)
        received: list[bytes] = []
        link = await transport.connect(backend.devices[2], lambda data: received.append(data))
        assert link.family == "multilink_gfdi_reliable"
        assert transport.multilink_client is not None

        await transport.send(b"gfdi-wire-out")
        reliable_writes = [raw for _, raw in backend.writes if raw and raw[0] & 0x80]
        assert reliable_writes
        assert b"".join(MlrPacket.parse(raw).payload for raw in reliable_writes) == b"gfdi-wire-out"

        assert backend.notification_callback is not None
        await backend.notification_callback(MlrPacket(0x80, b"gfdi-wire-in", True, 0, 1).encode())
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.005)
        assert received == [b"gfdi-wire-in"]
        await transport.disconnect()
        assert transport.state is TransportState.FINISHED

    asyncio.run(run())


def test_backend_pair_on_connect_path_precedes_service_discovery() -> None:
    class PairBeforeDiscoveryBackend(FakeBackend):
        async def connect_with_pairing(self, device: DiscoveredDevice) -> None:
            self.calls.append(("connect_with_pairing", device.address))
            self.bonded = True

    async def run() -> None:
        backend = PairBeforeDiscoveryBackend()
        transport = BleTransport(backend, require_bond=True)
        await transport.connect(backend.devices[2], lambda _: None)
        assert ("connect_with_pairing", "AA:02") in backend.calls
        assert "create_bond" not in backend.calls
        assert backend.calls.index(("connect_with_pairing", "AA:02")) < backend.calls.index("discover_services")
        assert transport.state is TransportState.AVAILABLE
        await transport.disconnect()

    asyncio.run(run())
