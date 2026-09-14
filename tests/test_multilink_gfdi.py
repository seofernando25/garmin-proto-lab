from __future__ import annotations

import asyncio

from garmin_proto_lab.mlr import MlrPacket
from garmin_proto_lab.multilink import (
    MULTILINK_PAIRED_CHARACTERISTICS,
    MULTILINK_PRIMARY_CHARACTERISTICS,
    MULTILINK_SERVICE_UUID,
    REGISTRATION_SERVICE_ID,
)
from garmin_proto_lab.multilink_client import MultiLinkClient
from garmin_proto_lab.multilink_gfdi import MultiLinkGfdiChannel
from garmin_proto_lab.transport import GattCharacteristic, GattService


class Backend:
    def __init__(self) -> None:
        self.callback = None
        self.writes: list[bytes] = []

    async def subscribe(self, _uuid, callback) -> None:
        self.callback = callback

    async def write(self, _uuid, data: bytes) -> None:
        raw = bytes(data)
        self.writes.append(raw)
        if self.callback is not None and raw and raw[0] == 1:
            if len(raw) >= 2 and raw[1] == 0:
                await self.callback(b"\x01\x00\x02")
            return
        if raw and raw[0] == 0 and self.callback is not None:
            command = raw[1]
            connection = raw[2:10]
            if command == 5:
                await self.callback(b"\x00\x06" + connection + b"\x00\x00\x00")
            elif command == 0:
                service = raw[10:12]
                service_id = int.from_bytes(service, "little")
                if service_id == REGISTRATION_SERVICE_ID:
                    handle, flags = 1, 0
                else:
                    handle, flags = 0x80, 1
                await self.callback(
                    b"\x00\x01" + connection + service + b"\x00" + bytes((handle, flags, 1))
                )
            elif command == 2:
                service = raw[10:12]
                await self.callback(b"\x00\x03" + connection + service + bytes((raw[12], 0)))


def services():
    return (
        GattService(
            MULTILINK_SERVICE_UUID,
            (
                GattCharacteristic(MULTILINK_PRIMARY_CHARACTERISTICS[0]),
                GattCharacteristic(MULTILINK_PAIRED_CHARACTERISTICS[0]),
            ),
        ),
    )


def test_reliable_multilink_gfdi_channel_round_trip() -> None:
    async def run() -> None:
        backend = Backend()
        client = MultiLinkClient(backend, services(), 0x1234, 20, timeout=0.2)  # type: ignore[arg-type]
        await client.initialize()
        logical = await client.open_gfdi_service()
        received: list[bytes] = []
        channel = MultiLinkGfdiChannel(client, logical, lambda data: received.append(data), receive_poll_seconds=0.005)
        await channel.start()

        await channel.send(b"outbound-gfdi")
        sent = [MlrPacket.parse(raw) for raw in backend.writes if raw and raw[0] & 0x80]
        assert sent
        assert b"".join(packet.payload for packet in sent) == b"outbound-gfdi"

        assert backend.callback is not None
        await backend.callback(MlrPacket(logical.handle, b"inbound-gfdi", True, 0, 1).encode())
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.005)
        assert received == [b"inbound-gfdi"]
        await channel.close()

    asyncio.run(run())
