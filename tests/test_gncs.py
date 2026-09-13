from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.gncs import (
    AncsSemanticError,
    ControlPointResponse,
    ControlPointResponseType,
    DataSourceChunk,
    DataSourceResponse,
    DataSourceStatus,
    GncsError,
    PHONE_NUMBER_FEATURE,
    SubscriptionIntent,
    SubscriptionRequest,
    SubscriptionResponse,
    SubscriptionStatus,
    build_data_source_chunk,
    decode_control_point_payload,
    encode_notification_source_payload,
)
from garmin_proto_lab.xxtea import decrypt_padded

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


def test_subscription_request_and_response() -> None:
    req = SubscriptionRequest(SubscriptionIntent.SUBSCRIBE, PHONE_NUMBER_FEATURE)
    assert req.encode() == bytes([1, 1])
    parsed_req = SubscriptionRequest.parse(req.encode())
    assert parsed_req.intent is SubscriptionIntent.SUBSCRIBE
    assert parsed_req.requests_phone_number_feature is True

    rsp = SubscriptionResponse(SubscriptionStatus.SUCCESSFUL, SubscriptionIntent.SUBSCRIBE, 1)
    assert rsp.encode() == bytes([0, 1, 1])
    parsed_rsp = SubscriptionResponse.parse(rsp.encode())
    assert parsed_rsp.status is SubscriptionStatus.SUCCESSFUL
    assert parsed_rsp.intent is SubscriptionIntent.SUBSCRIBE


def test_subscription_rejects_bad_lengths() -> None:
    with pytest.raises(GncsError):
        SubscriptionRequest.parse(b"\x01")
    with pytest.raises(GncsError):
        SubscriptionResponse.parse(b"\x00\x01")


def test_data_source_response_values() -> None:
    for status in DataSourceStatus:
        payload = DataSourceResponse(status).encode()
        assert DataSourceResponse.parse(payload).status is status


@given(
    st.integers(min_value=0, max_value=8192),
    st.integers(min_value=0, max_value=0xFFFF),
    st.integers(min_value=0, max_value=0xFFFF),
    st.binary(max_size=256),
)
def test_data_source_chunk_roundtrip(total: int, crc: int, offset: int, data: bytes) -> None:
    chunk = DataSourceChunk(total, crc, offset, data)
    assert DataSourceChunk.parse(chunk.encode()) == chunk


def test_unencrypted_chunk_packetization_and_crc_seed() -> None:
    source = bytes(range(100))
    built = build_data_source_chunk(source, data_offset=0, crc_seed=0, gfdi_payload_limit=40)
    # Static sender reserves 10 bytes from the GFDI payload limit.
    assert built.source_bytes_consumed == 30
    parsed = DataSourceChunk.parse(built.payload)
    assert parsed.total_payload_size == 100
    assert parsed.data_offset == 0
    assert parsed.transmitted_data == source[:30]
    assert parsed.transferred_crc == crc16_arc(source[:30])
    assert built.next_data_offset == 30

    second = build_data_source_chunk(
        source,
        data_offset=built.next_data_offset,
        crc_seed=built.next_crc_seed,
        gfdi_payload_limit=40,
    )
    parsed2 = DataSourceChunk.parse(second.payload)
    assert parsed2.transferred_crc == crc16_arc(source[30:60], built.next_crc_seed)


def test_encrypted_chunk_crc_covers_transmitted_bytes() -> None:
    source = b"a notification body that crosses a few words"
    built = build_data_source_chunk(source, data_offset=0, crc_seed=0x1234, gfdi_payload_limit=32, session_key=KEY)
    parsed = DataSourceChunk.parse(built.payload)
    assert parsed.transferred_crc == crc16_arc(parsed.transmitted_data, 0x1234)
    assert decrypt_padded(parsed.transmitted_data, KEY) == source[: built.source_bytes_consumed]
    assert len(parsed.transmitted_data) <= 32 - 6


def test_gncs_payload_limits_are_bounded() -> None:
    with pytest.raises(GncsError):
        build_data_source_chunk(b"", data_offset=0, crc_seed=0, gfdi_payload_limit=100)
    with pytest.raises(GncsError):
        build_data_source_chunk(b"x" * 8193, data_offset=0, crc_seed=0, gfdi_payload_limit=100)
    with pytest.raises(GncsError):
        build_data_source_chunk(b"abc", data_offset=3, crc_seed=0, gfdi_payload_limit=100)
    with pytest.raises(GncsError):
        build_data_source_chunk(b"abc", data_offset=0, crc_seed=0, gfdi_payload_limit=10)


def test_notification_source_optional_padded_encryption() -> None:
    source = bytes.fromhex("000206037856341202")
    assert encode_notification_source_payload(source) == source
    encrypted = encode_notification_source_payload(source, KEY)
    assert encrypted != source
    assert len(encrypted) % 4 == 0
    assert decrypt_padded(encrypted, KEY) == source
    with pytest.raises(GncsError, match="nine bytes"):
        encode_notification_source_payload(b"short")


def test_control_point_response_and_optional_decryption() -> None:
    response = ControlPointResponse(ControlPointResponseType.ANCS_ERROR_OCCURRED, AncsSemanticError.INVALID_PARAMETER)
    assert response.encode() == bytes([1, 162])
    assert ControlPointResponse.parse(response.encode()) == response

    clear = bytes.fromhex("020403020101")
    encrypted = encode_notification_source_payload(bytes.fromhex("000206037856341202"), KEY)
    # The helper accepts any padded GNCS control-point body, not just six-byte
    # actions; build a direct padded transform for this fixture.
    from garmin_proto_lab.xxtea import encrypt_padded
    protected = encrypt_padded(clear, KEY)
    assert decode_control_point_payload(protected, KEY) == clear
    assert decode_control_point_payload(clear) == clear
