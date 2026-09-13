from __future__ import annotations

import pytest

from garmin_proto_lab.protobuf_wire import (
    ProtobufWireError,
    WireType,
    decode_varint,
    encode_bool,
    encode_message,
    encode_string,
    encode_uint,
    encode_varint,
    last_bytes,
    last_varint,
    parse_fields,
)
from garmin_proto_lab.smart_proto import (
    CORE_CONNECTION_READY_NOTIFICATION,
    CORE_FEATURE_CAPABILITIES_RESPONSE,
    FEATURE_CAPABILITIES_GNCS_EXTENSION,
    SMART_CORE_EXTENSION,
    FeatureCapabilitiesRequest,
    GncsCapabilitiesAdvertisement,
    GuidStatus,
    NotificationDisabledReason,
    build_feature_capabilities_request,
    build_smart_extension,
    is_connection_ready_notification,
    parse_feature_capabilities_response,
    semantic_version_to_int,
)


def test_varint_vectors_and_wire_parser() -> None:
    vectors = {0: b"\x00", 1: b"\x01", 127: b"\x7f", 128: b"\x80\x01", 300: b"\xac\x02"}
    for value, raw in vectors.items():
        assert encode_varint(value) == raw
        assert decode_varint(raw) == (value, len(raw))

    payload = encode_uint(1, 300) + encode_string(3, "abc") + encode_bool(5, True)
    fields = parse_fields(payload)
    assert last_varint(fields, 1) == 300
    assert last_bytes(fields, 3) == b"abc"
    assert last_varint(fields, 5) == 1


def test_wire_parser_rejects_truncation_groups_and_bounds() -> None:
    with pytest.raises(ProtobufWireError, match="truncated varint"):
        parse_fields(b"\x08\x80")
    with pytest.raises(ProtobufWireError, match="unsupported protobuf wire type"):
        parse_fields(b"\x0b")  # start-group
    with pytest.raises(ProtobufWireError, match="configured bound"):
        parse_fields(b"\x0a\x05abcde", max_length_delimited=4)
    with pytest.raises(ProtobufWireError, match="field count"):
        parse_fields(b"\x08\x00\x10\x00", max_fields=1)


def test_gncs_feature_capabilities_request_nesting() -> None:
    request = FeatureCapabilitiesRequest(
        gncs=GncsCapabilitiesAdvertisement(
            semantic_version_to_int("7.4.30"),
            NotificationDisabledReason.PERMISSION_NOT_GRANTED,
            "sms.app",
            "dialer.app",
        )
    )
    smart = build_feature_capabilities_request(request)
    smart_fields = parse_fields(smart)
    core = last_bytes(smart_fields, SMART_CORE_EXTENSION)
    assert core is not None
    core_fields = parse_fields(core)
    feature = last_bytes(core_fields, 8)
    assert feature is not None
    feature_fields = parse_fields(feature)
    gncs = last_bytes(feature_fields, FEATURE_CAPABILITIES_GNCS_EXTENSION)
    assert gncs is not None
    gncs_fields = parse_fields(gncs)
    assert last_varint(gncs_fields, 1) == 0x07041E
    assert last_varint(gncs_fields, 2) == 2
    assert last_bytes(gncs_fields, 3) == b"sms.app"
    assert last_bytes(gncs_fields, 4) == b"dialer.app"


def test_gncs_feature_capabilities_response_and_connection_ready() -> None:
    gncs = encode_uint(1, 2) + encode_bool(2, True)
    feature = encode_uint(1, GuidStatus.MATCH) + encode_uint(2, 17) + encode_message(12, gncs)
    core = encode_message(CORE_FEATURE_CAPABILITIES_RESPONSE, feature)
    smart = build_smart_extension(SMART_CORE_EXTENSION, core)
    response = parse_feature_capabilities_response(smart)
    assert response.guid_status is GuidStatus.MATCH
    assert response.version == 17
    assert response.gncs is not None
    assert response.gncs.nc_version == 2
    assert response.gncs.support_blocked_apps is True
    assert response.gncs.modified_notifications_after_added is True

    ready = build_smart_extension(
        SMART_CORE_EXTENSION,
        encode_message(CORE_CONNECTION_READY_NOTIFICATION, b""),
    )
    assert is_connection_ready_notification(ready)
    assert not is_connection_ready_notification(smart)


def test_semantic_version_validation() -> None:
    assert semantic_version_to_int("1.2.3-beta") == 0x010203
    with pytest.raises(ValueError):
        semantic_version_to_int("1.2")
    with pytest.raises(ValueError):
        semantic_version_to_int("1.2.999")
