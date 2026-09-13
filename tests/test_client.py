from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone

import pytest

from garmin_proto_lab.ancs import ActionID, CategoryID, EventID, NotificationSource, PerformNotificationAction
from garmin_proto_lab.client import (
    GarminClient,
    NotificationPolicy,
    SemanticKind,
)
from garmin_proto_lab.dispatcher import UnknownMessage
from garmin_proto_lab.frame import Acknowledgement, Frame, ResponseStatus
from garmin_proto_lab.gncs import ControlPointResponse, DataSourceChunk, DataSourceStatus, SubscriptionResponse, SubscriptionStatus
from garmin_proto_lab.handshake import HostIdentity
from garmin_proto_lab.sync import SyncOption
from garmin_proto_lab.xxtea import decrypt_padded, encrypt_padded

NOW = datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc)
KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


def _device_info(protocol: int = 150, max_packet_size: int = 40) -> bytes:
    out = bytearray()
    out += protocol.to_bytes(2, "little")
    out += (1234).to_bytes(2, "little")
    out += (0x01020304).to_bytes(4, "little")
    out += (900).to_bytes(2, "little")
    out += max_packet_size.to_bytes(2, "little")
    for text in (b"Watch", b"Owner Watch", b"Model"):
        out += bytes([len(text)]) + text
    return bytes(out)


class FakeLink:
    def __init__(self) -> None:
        self.callback = None
        self.responses: list[tuple[Frame, ResponseStatus | int, bytes]] = []
        self.requests: list[tuple[int, bytes]] = []
        self.ack_payloads: deque[bytes] = deque()

    def set_incoming_callback(self, callback):
        self.callback = callback

    async def respond(self, frame, status=ResponseStatus.ACK, payload=b""):
        self.responses.append((frame, status, bytes(payload)))

    async def request(self, message_type, payload=b"", **kwargs):
        self.requests.append((message_type, bytes(payload)))
        response_payload = self.ack_payloads.popleft() if self.ack_payloads else b""
        return Acknowledgement(message_type, ResponseStatus.ACK, response_payload, 0)

    async def emit(self, message_type: int, payload: bytes):
        assert self.callback is not None
        await self.callback(UnknownMessage(Frame(message_type, payload), "test"))


def _client(link: FakeLink, flags=frozenset(), *, policy=NotificationPolicy()) -> GarminClient:
    return GarminClient(
        link,  # type: ignore[arg-type]
        HostIdentity(1, "Host", "Maker", "Model"),
        flags,
        notification_policy=policy,
        clock=lambda: NOW,
    )


async def _handshake(client: GarminClient, link: FakeLink, peer_flags: set[int]) -> None:
    await link.emit(5024, _device_info())
    if peer_flags:
        largest = max(peer_flags)
        raw = bytearray(largest // 8 + 1)
        for flag in peer_flags:
            raw[flag // 8] |= 1 << (flag % 8)
    else:
        raw = bytearray()
    await link.emit(5050, bytes([len(raw)]) + raw)
    await client.drain_background()
    assert client.handshake.complete


def test_semantic_handshake_battery_sync_and_file_events() -> None:
    async def run() -> None:
        link = FakeLink()
        client = _client(link, frozenset({3, 6, 71}))
        await _handshake(client, link, {3})
        assert link.responses[0][0].message_type == 5024
        assert int.from_bytes(link.responses[0][2][:2], "little") == 150
        assert link.responses[1][0].message_type == 5050
        assert link.responses[1][2] == b""
        assert any(message_type == 5050 for message_type, _ in link.requests)

        await link.emit(5023, bytes([0x20, 0, 87, 0, 0, 0]))
        await link.emit(5037, bytes([SyncOption.MANUAL, 1, 1 << 5]))
        file_ready = (
            (7).to_bytes(2, "little")
            + bytes([4, 32, 0, 0, 0xAA, 0x90])
            + (123).to_bytes(4, "little")
            + (456).to_bytes(4, "little")
        )
        await link.emit(5009, file_ready)

        events = [await client.events.get() for _ in range(5)]
        assert [event.kind for event in events] == [
            SemanticKind.DEVICE_INFO,
            SemanticKind.CONFIGURATION,
            SemanticKind.BATTERY,
            SemanticKind.SYNC_REQUEST,
            SemanticKind.FILE_READY,
        ]
        assert events[2].value.capacity_percent == 87
        assert events[3].value.known_categories
        assert events[4].value.file_index == 7

    asyncio.run(run())


def test_time_sync_legacy_and_request_time_paths() -> None:
    async def run_legacy() -> None:
        link = FakeLink()
        client = _client(link)
        await _handshake(client, link, {3})
        link.requests.clear()
        await client.sync_time()
        assert link.requests[0][0] == 5026
        assert link.requests[0][1][0] == 5

    async def run_request_mode() -> None:
        link = FakeLink()
        client = _client(link)
        await _handshake(client, link, {71})
        link.requests.clear()

        async def peer_time_request() -> None:
            await asyncio.sleep(0)
            await link.emit(5052, b"\x11\x22\x33\x44")

        peer = asyncio.create_task(peer_time_request())
        await client.sync_time(request_timeout=0.1)
        await peer
        assert link.requests[0] == (5030, bytes([16, 0]))
        time_responses = [item for item in link.responses if item[0].message_type == 5052]
        assert len(time_responses) == 1
        assert time_responses[0][2][:4] == b"\x11\x22\x33\x44"
        assert len(time_responses[0][2]) == 20

    asyncio.run(run_legacy())
    asyncio.run(run_request_mode())


def test_notification_subscription_policy_and_source_encryption() -> None:
    async def run() -> None:
        link = FakeLink()
        client = _client(link, policy=NotificationPolicy(enabled=True, phone_number_feature=False))
        await link.emit(5036, bytes([1, 1]))
        response = SubscriptionResponse.parse(link.responses[-1][2])
        assert response.status is SubscriptionStatus.SUCCESSFUL
        assert response.feature_flags == 0

        source = NotificationSource(EventID.ADDED, 0, CategoryID.EMAIL, 1, 123)
        await client.send_notification_source(source)
        assert link.requests[-1] == (5033, source.encode())

        client.session_key = KEY
        await client.send_notification_source(source)
        encrypted = link.requests[-1][1]
        assert encrypted != source.encode()
        assert decrypt_padded(encrypted, KEY) == source.encode()

    asyncio.run(run())


def test_notification_data_chunking_success_retry_and_abort() -> None:
    async def run_success_and_retry() -> None:
        link = FakeLink()
        client = _client(link)
        payload = bytes(range(70))
        # First chunk CRC mismatch -> resend same packet, then all chunks succeed.
        link.ack_payloads.extend(
            [
                bytes([DataSourceStatus.CRC_MISMATCH]),
                bytes([DataSourceStatus.TRANSFER_SUCCESSFUL]),
                bytes([DataSourceStatus.TRANSFER_SUCCESSFUL]),
                bytes([DataSourceStatus.TRANSFER_SUCCESSFUL]),
            ]
        )
        await client.send_notification_data(payload, gfdi_payload_limit=40)
        sent = [DataSourceChunk.parse(body) for msg, body in link.requests if msg == 5035]
        assert len(sent) == 4
        assert sent[0] == sent[1]
        assert [chunk.data_offset for chunk in sent] == [0, 0, 30, 60]

    async def run_abort() -> None:
        link = FakeLink()
        client = _client(link)
        link.ack_payloads.append(bytes([DataSourceStatus.ABORT_REQUEST]))
        with pytest.raises(Exception, match="aborted"):
            await client.send_notification_data(b"hello", gfdi_payload_limit=40)

    asyncio.run(run_success_and_retry())
    asyncio.run(run_abort())


def test_control_point_ack_validation_and_optional_decryption() -> None:
    async def run() -> None:
        link = FakeLink()
        client = _client(link)
        clear = PerformNotificationAction(123, ActionID.POSITIVE).encode()
        await link.emit(5034, clear)
        response = ControlPointResponse.parse(link.responses[-1][2])
        assert int(response.response_type) == 0
        assert int(response.ancs_error) == 0
        event = await client.events.get()
        assert event.kind is SemanticKind.GNCS_CONTROL_POINT
        assert event.value == PerformNotificationAction(123, ActionID.POSITIVE)

        client.session_key = KEY
        await link.emit(5034, encrypt_padded(clear, KEY))
        response = ControlPointResponse.parse(link.responses[-1][2])
        assert int(response.ancs_error) == 0
        event = await client.events.get()
        assert event.value.notification_id == 123

        client.session_key = None
        await link.emit(5034, b"\x44")
        response = ControlPointResponse.parse(link.responses[-1][2])
        assert int(response.response_type) == 1
        assert int(response.ancs_error) == 160

    asyncio.run(run())
