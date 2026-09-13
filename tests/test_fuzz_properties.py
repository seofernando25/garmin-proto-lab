from __future__ import annotations

from hypothesis import given, settings, strategies as st

from garmin_proto_lab.ancs import (
    AncsError,
    GetAppAttributesRequest,
    GetAppAttributesResponse,
    GetNotificationAttributesRequest,
    GetNotificationAttributesResponse,
    NotificationActions,
    NotificationSource,
    PerformAndroidAction,
    PerformNotificationAction,
)
from garmin_proto_lab.battery import parse_battery_status
from garmin_proto_lab.cobs import CobsError, decode as cobs_decode
from garmin_proto_lab.codec import DecodeProblem, DecodedPacket, GfdiWireCodec
from garmin_proto_lab.configuration import ConfigurationError, parse_configuration
from garmin_proto_lab.device_info import DeviceInfoError, parse_device_information
from garmin_proto_lab.filetransfer import (
    FileTransferError,
    parse_compressed_file_data,
    parse_download_response,
    parse_file_data,
    parse_supported_file_types,
)
from garmin_proto_lab.frame import Frame, FrameError, decode_frame
from garmin_proto_lab.gncs import DataSourceChunk, GncsError, SubscriptionRequest, SubscriptionResponse
from garmin_proto_lab.protobuf_transport import ProtobufChunk, ProtobufChunkAck, ProtobufTransportError
from garmin_proto_lab.sync import FileReady, QueuedDownload, SyncError, SyncRequest


@settings(max_examples=150, deadline=None)
@given(st.integers(min_value=0, max_value=0x7FFF), st.binary(max_size=300), st.lists(st.integers(1, 64), min_size=1, max_size=30))
def test_wire_codec_roundtrip_under_arbitrary_ble_fragmentation(
    message_type: int, payload: bytes, chunk_sizes: list[int]
) -> None:
    codec = GfdiWireCodec()
    wire = codec.encode(Frame(message_type, payload))
    decoder = GfdiWireCodec()
    results = []
    pos = 0
    index = 0
    while pos < len(wire):
        size = chunk_sizes[index % len(chunk_sizes)]
        index += 1
        results.extend(decoder.feed(wire[pos : pos + size]))
        pos += size
    assert len(results) == 1
    assert isinstance(results[0], DecodedPacket)
    assert results[0].frame == Frame(message_type, payload)


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=512))
def test_raw_frame_parser_never_raises_unexpected_exception(data: bytes) -> None:
    try:
        decode_frame(data)
    except FrameError:
        pass


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=512).filter(lambda value: b"\x00" not in value))
def test_cobs_parser_is_bounded_and_fail_closed(data: bytes) -> None:
    try:
        cobs_decode(data)
    except CobsError:
        pass


@settings(max_examples=160, deadline=None)
@given(st.binary(max_size=160))
def test_semantic_parsers_do_not_leak_index_errors(data: bytes) -> None:
    parsers = [
        parse_configuration,
        parse_device_information,
        parse_battery_status,
        parse_download_response,
        parse_file_data,
        parse_compressed_file_data,
        parse_supported_file_types,
        NotificationSource.parse,
        GetNotificationAttributesRequest.parse,
        GetNotificationAttributesResponse.parse,
        GetAppAttributesRequest.parse,
        GetAppAttributesResponse.parse,
        PerformNotificationAction.parse,
        PerformAndroidAction.parse,
        NotificationActions.parse,
        SubscriptionRequest.parse,
        SubscriptionResponse.parse,
        DataSourceChunk.parse,
        SyncRequest.parse,
        QueuedDownload.parse,
        FileReady.parse,
        ProtobufChunk.parse,
        ProtobufChunkAck.parse,
    ]
    expected = (
        ValueError,
        ConfigurationError,
        DeviceInfoError,
        FileTransferError,
        AncsError,
        GncsError,
        SyncError,
        ProtobufTransportError,
    )
    for parser in parsers:
        try:
            parser(data)
        except expected:
            pass


@settings(max_examples=120, deadline=None)
@given(st.binary(max_size=300))
def test_incremental_wire_decoder_surfaces_damage_instead_of_throwing(data: bytes) -> None:
    # Sandwich arbitrary noise between delimiters so it is considered one COBS
    # candidate. The public wire codec should surface DecodeProblem, not leak a
    # parser exception or grow without a bound.
    codec = GfdiWireCodec(max_encoded_packet=512)
    try:
        results = codec.feed(b"\x00" + data + b"\x00")
    except CobsError as exc:  # pragma: no cover - this is precisely what must not happen
        raise AssertionError("wire codec leaked CobsError") from exc
    assert all(isinstance(item, (DecodedPacket, DecodeProblem)) for item in results)
