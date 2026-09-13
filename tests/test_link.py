from __future__ import annotations

import asyncio

import pytest

from garmin_proto_lab.codec import DecodedPacket, GfdiWireCodec
from garmin_proto_lab.frame import Frame, ResponseStatus, decode_frame, encode_ack
from garmin_proto_lab.link import GfdiMessageLink, RequestTimeout, ResponseRejected
from garmin_proto_lab.transport import BleTransport, DiscoveredDevice, GattCharacteristic, GattService
from garmin_proto_lab.uuids import CONNECT_MOBILE_READ, CONNECT_MOBILE_SERVICE, CONNECT_MOBILE_WRITE


class PeerBackend:
    def __init__(self) -> None:
        self.callback = None
        self.peer_codec = GfdiWireCodec()
        self.respond = True
        self.status = ResponseStatus.ACK
        self.response_payload = b"reply"
        self.requests: list[Frame] = []

    async def scan(self, timeout: float):
        return [DiscoveredDevice("AA", "watch", frozenset({CONNECT_MOBILE_SERVICE}), -10)]

    async def connect(self, device):
        return None

    async def disconnect(self):
        return None

    async def is_bonded(self):
        return True

    async def create_bond(self):
        return None

    async def discover_services(self):
        return [
            GattService(
                CONNECT_MOBILE_SERVICE,
                (GattCharacteristic(CONNECT_MOBILE_WRITE), GattCharacteristic(CONNECT_MOBILE_READ)),
            )
        ]

    async def request_mtu(self, mtu: int):
        return 64

    async def subscribe(self, uuid, callback):
        self.callback = callback

    async def write(self, uuid, data: bytes):
        for result in self.peer_codec.feed(data):
            assert isinstance(result, DecodedPacket)
            request = result.frame
            self.requests.append(request)
            if self.respond:
                ack_frame = decode_frame(
                    encode_ack(request.message_type, self.status, self.response_payload, request.transaction_id)
                )
                wire = self.peer_codec.encode(ack_frame)
                assert self.callback is not None
                await self.callback(wire)


async def _connected_link() -> tuple[GfdiMessageLink, PeerBackend]:
    backend = PeerBackend()
    transport = BleTransport(backend, require_bond=False)
    link = GfdiMessageLink(transport, request_timeout=0.05)
    await link.connect(DiscoveredDevice("AA", "watch", frozenset({CONNECT_MOBILE_SERVICE}), -10))
    return link, backend


def test_request_response_correlation_end_to_end_through_wire_codec() -> None:
    async def run() -> None:
        link, backend = await _connected_link()
        ack = await link.request(5023, b"phone-battery")
        assert ack.request_type == 5023
        assert ack.payload == b"reply"
        assert backend.requests[0].message_type == 5023
        assert backend.requests[0].transaction_id == 0
        assert link.tracker.pending == ()
        await link.disconnect()

    asyncio.run(run())


def test_rejected_response_surfaces_semantic_error() -> None:
    async def run() -> None:
        link, backend = await _connected_link()
        backend.status = ResponseStatus.LENGTH_ERROR
        with pytest.raises(ResponseRejected) as exc:
            await link.request(5052, b"bad")
        assert exc.value.acknowledgement.status is ResponseStatus.LENGTH_ERROR
        assert link.tracker.pending == ()

    asyncio.run(run())


def test_request_timeout_cleans_tracker_and_waiter() -> None:
    async def run() -> None:
        link, backend = await _connected_link()
        backend.respond = False
        with pytest.raises(RequestTimeout, match="5024"):
            await link.request(5024, b"", timeout=0.01)
        assert link.tracker.pending == ()
        assert link._pending == {}

    asyncio.run(run())


def test_unknown_incoming_message_is_preserved() -> None:
    async def run() -> None:
        link, backend = await _connected_link()
        assert backend.callback is not None
        incoming = GfdiWireCodec().encode(Frame(29999, b"mystery"))
        await backend.callback(incoming)
        item = await asyncio.wait_for(link.incoming.get(), 0.1)
        assert item.frame.message_type == 29999
        assert item.frame.payload == b"mystery"
        assert link.problems == []

    asyncio.run(run())
