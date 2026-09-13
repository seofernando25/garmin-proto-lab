from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.cobs import CobsError, StreamDecoder, decode, encode, frame
from garmin_proto_lab.crc import crc16_arc


def test_crc16_arc_check_vector() -> None:
    assert crc16_arc(b"123456789") == 0xBB3D


@pytest.mark.parametrize(
    ("plain", "encoded"),
    [
        (b"", bytes.fromhex("01")),
        (bytes([0]), bytes.fromhex("0101")),
        (bytes.fromhex("11220033"), bytes.fromhex("0311220233")),
        (bytes.fromhex("112233"), bytes.fromhex("04112233")),
    ],
)
def test_cobs_known_vectors(plain: bytes, encoded: bytes) -> None:
    assert encode(plain) == encoded
    assert decode(encoded) == plain


@given(st.binary(max_size=2048))
def test_cobs_roundtrip(payload: bytes) -> None:
    assert decode(encode(payload)) == payload


def test_cobs_stream_decoder_across_ble_boundaries() -> None:
    payloads = [b"alpha\x00beta", bytes(range(1, 120)), b"tail"]
    stream = b"noise" + b"".join(frame(p) for p in payloads)
    decoder = StreamDecoder()
    out: list[bytes] = []
    # Deliberately split in places unrelated to COBS packet boundaries.
    for start in range(0, len(stream), 7):
        out.extend(decoder.feed(stream[start : start + 7]))
    assert out == payloads


def test_cobs_rejects_zero_inside_encoded_packet() -> None:
    with pytest.raises(CobsError):
        decode(bytes.fromhex("020001"))
