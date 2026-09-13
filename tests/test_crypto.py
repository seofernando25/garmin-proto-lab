from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.frame import Frame, encode_frame
from garmin_proto_lab.pairing import confirm_value, mac_block, session_key, short_term_key
from garmin_proto_lab.secure_session import SecurePacketDecoder, SecurePacketError, encrypt_packet
from garmin_proto_lab.xxtea import XxteaError, decrypt, decrypt_padded, encrypt, encrypt_padded


KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


def test_xxtea_cross_checked_reference_vector() -> None:
    # Cross-checked once against the published Wheeler/Needham btea reference
    # using uint32_t words and little-endian byte conversion.
    plain = bytes(range(16))
    expected = bytes.fromhex("d0a054aabc1098ba38604d5a9ee4e402")
    assert encrypt(plain, KEY) == expected
    assert decrypt(expected, KEY) == plain


@given(st.binary(min_size=8, max_size=256).filter(lambda b: len(b) % 4 == 0))
def test_xxtea_roundtrip(data: bytes) -> None:
    assert decrypt(encrypt(data, KEY), KEY) == data


def test_xxtea_rejects_bad_lengths() -> None:
    with pytest.raises(XxteaError):
        encrypt(b"123", KEY)
    with pytest.raises(XxteaError):
        encrypt(b"12345678", b"short")


def test_one_word_xxtea_is_rejected_as_noninvertible() -> None:
    with pytest.raises(XxteaError):
        encrypt(b"1234", KEY)


@given(st.binary(min_size=3, max_size=128))
def test_gncs_padded_xxtea_roundtrip(data: bytes) -> None:
    encrypted = encrypt_padded(data, KEY)
    assert len(encrypted) % 4 == 0
    assert decrypt_padded(encrypted, KEY) == data


def test_mac_block_reverses_mac_and_zero_extends() -> None:
    assert mac_block("01:23:45:67:89:AB") == bytes.fromhex("ab8967452301") + bytes(10)


def test_pairing_derivations_are_deterministic() -> None:
    passkey = bytes.fromhex("30313233343536373839303132333435")
    host_random = bytes(range(16))
    device_random = bytes(range(16, 32))
    confirm = confirm_value("01:23:45:67:89:AB", passkey, host_random)
    stk = short_term_key(passkey, host_random, device_random)
    sk = session_key(KEY, bytes.fromhex("0102030405060708"), bytes.fromhex("1112131415161718"))
    assert confirm.hex() == "f12122633d5b80b9e74b333256651d60"
    assert stk.hex() == "a1bc4d6278ccd769e8728d133916e32d"
    assert sk.hex() == "dc5c7bc1127b03cbf0a7a3ff24e7c9da"


def test_secure_session_roundtrip_and_counter_guard() -> None:
    iv = bytes.fromhex("a1b2c3d4")
    inner0 = encode_frame(Frame(5052, b"hello"))
    inner1 = encode_frame(Frame(5023, b"world!"))
    decoder = SecurePacketDecoder(KEY, iv)
    assert decoder.decrypt(encrypt_packet(inner0, KEY, iv, 3)) == inner0
    assert decoder.decrypt(encrypt_packet(inner1, KEY, iv, 4)) == inner1
    with pytest.raises(SecurePacketError, match="backwards"):
        decoder.decrypt(encrypt_packet(inner0, KEY, iv, 2))


def test_secure_session_rejects_wrong_iv() -> None:
    inner = encode_frame(Frame(5024, b"x"))
    decoder = SecurePacketDecoder(KEY, bytes.fromhex("01020304"))
    with pytest.raises(SecurePacketError, match="vector mismatch"):
        decoder.decrypt(encrypt_packet(inner, KEY, bytes.fromhex("aabbccdd"), 0))
