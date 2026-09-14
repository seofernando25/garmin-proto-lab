from __future__ import annotations

import asyncio

import pytest

from garmin_proto_lab.client import GarminClient, SemanticKind
from garmin_proto_lab.dispatcher import UnknownMessage
from garmin_proto_lab.frame import Frame, ResponseStatus
from garmin_proto_lab.handshake import HostIdentity
from garmin_proto_lab.protobuf_wire import encode_bool, encode_message, encode_uint, last_bytes, last_varint, parse_fields
from garmin_proto_lab.smart_proto import (
    CORE_CONNECTION_READY_NOTIFICATION,
    CORE_FEATURE_CAPABILITIES_RESPONSE,
    FEATURE_CAPABILITIES_GNCS_EXTENSION,
    SMART_CORE_EXTENSION,
    build_smart_extension,
)


def _device_info(max_packet_size: int = 64) -> bytes:
    out = bytearray()
    out += (150).to_bytes(2, "little")
    out += (1234).to_bytes(2, "little")
    out += (1).to_bytes(4, "little")
    out += (900).to_bytes(2, "little")
    out += max_packet_size.to_bytes(2, "little")
    for text in (b"Watch", b"Watch", b"Model"):
        out += bytes([len(text)]) + text
    return bytes(out)


class FakeLink:
    def __init__(self) -> None:
        self.callback = None
        self.responses = []
        self.requests = []

    def set_incoming_callback(self, callback):
        self.callback = callback

    async def respond(self, frame, status=ResponseStatus.ACK, payload=b""):
        self.responses.append((frame, status, bytes(payload)))

    async def request(self, message_type, payload=b"", **kwargs):
        self.requests.append((message_type, bytes(payload)))
        from garmin_proto_lab.frame import Acknowledgement
        return Acknowledgement(message_type, ResponseStatus.ACK, b"", 0)

    async def emit(self, message_type: int, payload: bytes):
        assert self.callback is not None
        await self.callback(UnknownMessage(Frame(message_type, payload), "test"))


class StubProtobuf:
    def __init__(self, link, response: bytes) -> None:
        self.link = link
        self.response = response
        self.request_handler = None
        self.payload_limit = None
        self.sent = []

    def set_payload_limit(self, value: int) -> None:
        self.payload_limit = value

    async def handle(self, frame: Frame) -> bool:
        return False

    async def request(self, payload: bytes) -> bytes:
        self.sent.append(payload)
        return self.response


def _feature_response(*, file_access: bool = False) -> bytes:
    gncs = encode_uint(1, 3) + encode_bool(2, True)
    feature = bytearray(encode_uint(1, 1) + encode_uint(2, 9) + encode_message(12, gncs))
    if file_access:
        feature += encode_message(16, encode_uint(3, 1) + encode_uint(4, 123456) + encode_bool(5, True))
    core = encode_message(CORE_FEATURE_CAPABILITIES_RESPONSE, bytes(feature))
    return build_smart_extension(SMART_CORE_EXTENSION, core)


def _client(link: FakeLink, protobuf: StubProtobuf, host_flags=frozenset({6, 71})) -> GarminClient:
    return GarminClient(
        link,  # type: ignore[arg-type]
        HostIdentity(1, "Host", "Independent", "Client"),
        host_flags,
        protobuf=protobuf,  # type: ignore[arg-type]
    )


async def _handshake(client: GarminClient, link: FakeLink, peer_flags: set[int]) -> None:
    await link.emit(5024, _device_info())
    raw = bytearray(max(peer_flags, default=0) // 8 + 1 if peer_flags else 0)
    for flag in peer_flags:
        raw[flag // 8] |= 1 << (flag % 8)
    await link.emit(5050, bytes([len(raw)]) + raw)
    await client.drain_background()


def test_device_info_sets_protobuf_limit_and_feature_capabilities_are_semantic() -> None:
    async def run() -> None:
        link = FakeLink()
        proto = StubProtobuf(link, _feature_response())
        client = _client(link, proto)
        await _handshake(client, link, {6, 95})
        assert proto.payload_limit == 64

        response = await client.refresh_feature_capabilities(gncs_version="0.1.2")
        assert response.version == 9
        assert response.gncs is not None
        assert response.gncs.nc_version == 3
        assert response.gncs.support_blocked_apps is True

        # Verify that we advertise our own semantic version, not Garmin's.
        smart_fields = parse_fields(proto.sent[-1])
        core = last_bytes(smart_fields, 13)
        assert core is not None
        feature = last_bytes(parse_fields(core), 8)
        assert feature is not None
        gncs = last_bytes(parse_fields(feature), 12)
        assert gncs is not None
        assert last_varint(parse_fields(gncs), 1) == 0x000102

        events = []
        while not client.events.empty():
            events.append(await client.events.get())
        assert events[-1].kind is SemanticKind.FEATURE_CAPABILITIES
        assert events[-1].accessible_text() == "Watch feature capabilities received"
    asyncio.run(run())


def test_feature_capability_exchange_is_gated_by_peer_flag_and_host_extensions() -> None:
    async def run_peer_gate() -> None:
        link = FakeLink()
        proto = StubProtobuf(link, _feature_response())
        client = _client(link, proto)
        await _handshake(client, link, {6})
        with pytest.raises(Exception, match="flag 95"):
            await client.refresh_feature_capabilities()

    async def run_no_optional_extensions() -> None:
        link = FakeLink()
        proto = StubProtobuf(link, _feature_response())
        client = _client(link, proto, frozenset({71}))
        await _handshake(client, link, {95})
        await client.refresh_feature_capabilities()
        smart_fields = parse_fields(proto.sent[-1])
        core = last_bytes(smart_fields, 13)
        feature = last_bytes(parse_fields(core or b""), 8)
        fields = parse_fields(feature or b"")
        assert not any(field.number in (12, 16) for field in fields)

    asyncio.run(run_peer_gate())
    asyncio.run(run_no_optional_extensions())


def test_connection_ready_protobuf_listener_is_handled_without_semantic_response() -> None:
    async def run() -> None:
        link = FakeLink()
        proto = StubProtobuf(link, _feature_response())
        client = _client(link, proto)
        assert proto.request_handler is not None
        ready = build_smart_extension(
            SMART_CORE_EXTENSION,
            encode_message(CORE_CONNECTION_READY_NOTIFICATION, b""),
        )
        handled = await proto.request_handler(33, ready)
        assert handled is True
        event = await client.events.get()
        assert event.kind is SemanticKind.CONNECTION_READY
        assert event.value == {"request_id": 33}
    asyncio.run(run())


def test_file_access_feature_capabilities_are_advertised_only_for_host_flag_90() -> None:
    async def run() -> None:
        link = FakeLink()
        proto = StubProtobuf(link, _feature_response(file_access=True))
        client = _client(link, proto, frozenset({71, 90}))
        await _handshake(client, link, {95})
        response = await client.refresh_feature_capabilities()
        assert response.file_access is not None
        assert client.file_access_capabilities is not None
        assert int(client.file_access_capabilities.server_file_checksum_method) == 1
        assert client.file_access_capabilities.checksum_max_file_size_byte == 123456
        assert client.file_access_capabilities.custom_flag_support is True

        smart_fields = parse_fields(proto.sent[-1])
        core = last_bytes(smart_fields, 13)
        feature = last_bytes(parse_fields(core or b""), 8)
        fields = parse_fields(feature or b"")
        assert last_bytes(fields, 12) is None
        # Empty extension is still present and therefore advertises the handler.
        assert last_bytes(fields, 16) == b""
    asyncio.run(run())


def test_file_access_runtime_gate_is_peer_configuration_90_not_feature_capability_95() -> None:
    async def run() -> None:
        link = FakeLink()
        proto = StubProtobuf(link, _feature_response())
        client = _client(link, proto, frozenset({71, 90}))
        await _handshake(client, link, {90})
        assert client.peer_supports_file_access is True
        assert client.peer_supports_feature_capabilities is False
        assert client.file_access_capabilities is None

        link2 = FakeLink()
        proto2 = StubProtobuf(link2, _feature_response(file_access=True))
        client2 = _client(link2, proto2, frozenset({71, 90}))
        await _handshake(client2, link2, {90, 95})
        assert client2.peer_supports_file_access is True
        assert client2.peer_supports_feature_capabilities is True
        await client2.refresh_feature_capabilities()
        assert client2.file_access_capabilities is not None

    asyncio.run(run())
