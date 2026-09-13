from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.frame import (
    ACK_MESSAGE_ID,
    Frame,
    FrameError,
    ResponseStatus,
    decode_frame,
    encode_ack,
    encode_frame,
    parse_ack,
)


@given(
    st.integers(min_value=0, max_value=0x7FFF),
    st.binary(max_size=1024),
)
def test_plain_frame_roundtrip(message_type: int, payload: bytes) -> None:
    frame = Frame(message_type, payload)
    assert decode_frame(encode_frame(frame)) == frame


@given(
    st.integers(min_value=5000, max_value=5255),
    st.integers(min_value=0, max_value=31),
    st.binary(max_size=1024),
)
def test_transaction_frame_roundtrip(message_type: int, transaction_id: int, payload: bytes) -> None:
    frame = Frame(message_type, payload, transaction_id)
    assert decode_frame(encode_frame(frame)) == frame


def test_ack_payload() -> None:
    raw = encode_ack(5024, ResponseStatus.ACK, b"abc", transaction_id=7)
    decoded = decode_frame(raw)
    assert decoded.message_type == ACK_MESSAGE_ID
    parsed = parse_ack(decoded)
    assert parsed.request_type == 5024
    assert parsed.status is ResponseStatus.ACK
    assert parsed.payload == b"abc"
    assert parsed.transaction_id == 7


def test_crc_corruption_rejected() -> None:
    raw = bytearray(encode_frame(Frame(5052, b"abcdef")))
    raw[4] ^= 0x80
    with pytest.raises(FrameError, match="CRC mismatch"):
        decode_frame(bytes(raw))


def test_length_corruption_rejected() -> None:
    raw = bytearray(encode_frame(Frame(5023, b"abcdef")))
    raw[0] ^= 1
    with pytest.raises(FrameError, match="length mismatch"):
        decode_frame(bytes(raw))


def test_compact_mode_rejects_out_of_range_type() -> None:
    with pytest.raises(FrameError):
        encode_frame(Frame(6000, b"", transaction_id=1))
