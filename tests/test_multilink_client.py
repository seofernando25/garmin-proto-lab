from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from garmin_proto_lab.mlr import MlrPacket
from garmin_proto_lab.multilink import (
    MULTILINK_PAIRED_CHARACTERISTICS,
    MULTILINK_PRIMARY_CHARACTERISTICS,
    MULTILINK_SERVICE_UUID,
    REGISTRATION_SERVICE_ID,
)
from garmin_proto_lab.multilink_client import MultiLinkClient, MultiLinkClientError
from garmin_proto_lab.transport import GattCharacteristic, GattService


class FakeBackend:
    def __init__(self) -> None:
        self.callbacks = {}
        self.writes: list[tuple[UUID, bytes]] = []

    async def subscribe(self, characteristic_uuid, callback) -> None:
        self.callbacks[characteristic_uuid] = callback

    async def write(self, characteristic_uuid, data: bytes) -> None:
        raw = bytes(data)
        self.writes.append((characteristic_uuid, raw))
        if not raw:
            return
        notify = MULTILINK_PRIMARY_CHARACTERISTICS[0]
        callback = self.callbacks[notify]
        if raw[0] == 1:
            if len(raw) >= 2 and raw[1] == 0:
                await callback(b"\x01\x00\x02")  # page 0: service ID 1 supported
            elif len(raw) >= 2 and raw[1] == 1:
                await callback(b"\x01\x01\x00\x13\x40")
            elif len(raw) >= 2 and raw[1] == 2:
                await callback(b"\x01\x02\x01\x02\x02")  # v2.2.1
            elif len(raw) >= 2 and raw[1] == 3:
                await callback(b"\x01\x03\x04\x0c\x14\x05\xef\xbe\xad\xde")
            elif len(raw) >= 2 and raw[1] == 4:
                await callback(b"\x01\x04" + bytes(range(16)))
            elif len(raw) >= 4 and raw[1] == 5:
                await callback(b"\x01" + raw[1:4] + b"\x00\x07")
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
            await callback(b"\x00\x01" + connection + service + b"\x00" + bytes((handle, flags, 7)))
        elif command == 2:
            service = raw[10:12]
            handle = raw[12]
            await callback(b"\x00\x03" + connection + service + bytes((handle, 0)))


def _services() -> tuple[GattService, ...]:
    return (
        GattService(
            MULTILINK_SERVICE_UUID,
            (
                GattCharacteristic(MULTILINK_PRIMARY_CHARACTERISTICS[0]),
                GattCharacteristic(MULTILINK_PAIRED_CHARACTERISTICS[0]),
            ),
        ),
    )


def test_initialize_register_file_transfer_route_data_and_close() -> None:
    async def run() -> None:
        backend = FakeBackend()
        client = MultiLinkClient(backend, _services(), 0x0102030405060708, 20)  # type: ignore[arg-type]
        registration = await client.initialize()
        assert registration.service_id == REGISTRATION_SERVICE_ID
        assert registration.handle == 1
        assert client.supported_services == frozenset({1})
        assert await client.query_service_revision(1) == 7
        assert client.service_revisions[1] == 7
        info = await client.query_registration_info()
        assert info.advertising_service_data == b"\x00\x13\x40"
        assert info.version is not None
        assert (info.version.major, info.version.minor, info.version.micro) == (2, 2, 1)
        assert info.product is not None
        assert info.product.product_number == 3076
        assert info.product.firmware_version == 1300
        assert info.product.unit_id == 0xDEADBEEF
        assert info.identity_address == bytes(range(16))
        assert client.notify_uuid == MULTILINK_PRIMARY_CHARACTERISTICS[0]
        assert client.write_uuid == MULTILINK_PAIRED_CHARACTERISTICS[0]

        service = await client.open_file_transfer_service()
        assert service.handle == 0x80
        assert service.reliable is True
        assert service.revision == 7

        raw = MlrPacket(0x80, b"abc", True, 0, 0).encode()
        await backend.callbacks[client.notify_uuid](raw)
        assert await client.recv_raw(0x80) == raw

        await client.send_raw(MlrPacket(0x80, b"ack", True, 0, 1).encode())
        assert backend.writes[-1][0] == client.write_uuid

        closed = await client.close_handle(service.service_id, service.handle)
        assert int(closed.status) == 0

    asyncio.run(run())


def test_multilink_rejects_missing_service_and_oversize_packet() -> None:
    async def run_missing() -> None:
        client = MultiLinkClient(FakeBackend(), (), 1, 20)  # type: ignore[arg-type]
        with pytest.raises(MultiLinkClientError, match="did not expose"):
            await client.initialize()

    async def run_oversize() -> None:
        backend = FakeBackend()
        client = MultiLinkClient(backend, _services(), 1, 4)  # type: ignore[arg-type]
        await client.initialize()
        with pytest.raises(MultiLinkClientError, match="exceeds"):
            await client.send_raw(b"12345")

    asyncio.run(run_missing())
    asyncio.run(run_oversize())


def test_open_gfdi_service_accepts_reliable_registration() -> None:
    async def run() -> None:
        backend = FakeBackend()
        client = MultiLinkClient(backend, _services(), 0x0102030405060708, 20)  # type: ignore[arg-type]
        await client.initialize()
        service = await client.open_gfdi_service()
        assert service.service_id == 1
        assert service.handle == 0x80
        assert service.reliable is True
        assert service.revision == 7

    asyncio.run(run())
