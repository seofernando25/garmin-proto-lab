from __future__ import annotations

import pytest

from garmin_proto_lab.auth_messages import (
    AuthCodecError,
    NegotiationRequest,
    NegotiationResponse,
    PasskeyMode,
    SessionKeyExchange,
    StkBeginRequest,
    StkBeginResponse,
    build_confirm_request,
    build_ltk_reconnect_request,
    build_secure_session_response,
    build_session_verification_response,
    build_stk_generation_status,
    decrypt_ltk_distribution,
    decrypt_secure_session_request,
    derive_session_key,
    parse_confirm_response,
    parse_random_response,
    verify_session_key_message,
)
from garmin_proto_lab.xxtea import decrypt, encrypt

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")


def test_5101_negotiation_layout() -> None:
    req = NegotiationRequest.parse(bytes([0]) + (1).to_bytes(4, "little"))
    assert req.prefix == 0
    assert req.algorithm_mask == 1
    assert req.encode() == bytes([0, 1, 0, 0, 0])
    rsp = NegotiationResponse.from_request(req, has_persistent_ltk=True)
    assert rsp.encode() == bytes([0, 1, 1, 0, 0, 0])

    no_common = NegotiationResponse.from_request(NegotiationRequest(0, 2), False)
    assert no_common.common_algorithm_status == 1


def test_5103_stk_begin_layout_and_valid_modes() -> None:
    raw = bytes([9]) + (0).to_bytes(2, "little") + bytes.fromhex("aabb") + bytes([PasskeyMode.VISIBLE])
    req = StkBeginRequest.parse(raw)
    assert req.prefix == 9
    assert req.timeout_seconds == 0
    assert req.effective_timeout_seconds == 30
    assert req.unknown_3_4 == bytes.fromhex("aabb")
    assert req.mode is PasskeyMode.VISIBLE
    assert StkBeginResponse.for_request(req).encode() == bytes([0, 0xFF])

    unsupported = StkBeginRequest.parse(bytes([0, 1, 0, 0, 0, 9]))
    assert StkBeginResponse.for_request(unsupported).encode() == bytes([0, 9])


def test_5104_and_5105_response_parsing() -> None:
    value = bytes(range(16))
    assert build_confirm_request(value) == value
    assert parse_confirm_response(bytes([0]) + value).value16 == value
    assert parse_random_response(bytes([2])).status == 2
    assert parse_random_response(bytes([2])).value16 is None
    with pytest.raises(AuthCodecError):
        parse_confirm_response(bytes([0]) + value[:8])


def test_5106_status_and_5102_reconnect_layout() -> None:
    assert build_stk_generation_status(True) == bytes([0])
    assert build_stk_generation_status(False) == bytes([1])
    ediv = bytes.fromhex("3412")
    rand = bytes.fromhex("0011223344556677")
    assert build_ltk_reconnect_request(ediv, rand) == ediv + rand


def test_5107_ltk_distribution_decryption() -> None:
    ltk = bytes(range(16))
    ediv = bytes.fromhex("a1b2")
    rand = bytes.fromhex("1020304050607080")
    reserved = bytes.fromhex("010203040506")
    plain = ltk + ediv + rand + reserved
    payload = bytes([0x77]) + encrypt(plain, KEY)
    parsed = decrypt_ltk_distribution(payload, KEY)
    assert parsed.ignored_prefix == 0x77
    assert parsed.long_term_key == ltk
    assert parsed.encrypted_diversifier == ediv
    assert parsed.random_number == rand
    assert parsed.reserved == reserved


def test_5108_session_key_distribution_and_derivation() -> None:
    device = bytes.fromhex("0102030405060708")
    host = bytes.fromhex("1112131415161718")
    exchange = SessionKeyExchange.parse_request(device)
    assert exchange.build_response(host) == bytes([0]) + host
    assert derive_session_key(KEY, device, host).hex() == "dc5c7bc1127b03cbf0a7a3ff24e7c9da"


def test_5109_session_verification() -> None:
    tail = bytes.fromhex("1020304050607080")
    payload = bytes([0x55]) + encrypt(KEY + tail, KEY)
    parsed, ok = verify_session_key_message(payload, KEY)
    assert ok is True
    assert parsed.ignored_prefix == 0x55
    assert parsed.echoed_session_key == KEY
    assert parsed.unknown_tail8 == tail
    assert build_session_verification_response(True) == bytes([0])
    assert build_session_verification_response(False) == bytes([2])
    assert build_session_verification_response(False, has_session_key=False) == bytes([1])


def test_5111_secure_session_request_response() -> None:
    device_iv = bytes.fromhex("a1b2c3d4")
    request_plain = bytes([7]) + bytes(7) + device_iv + bytes.fromhex("01020304")
    request_cipher = encrypt(request_plain, KEY)
    request = decrypt_secure_session_request(request_cipher, KEY)
    assert request.selector == 7
    assert request.device_iv4 == device_iv

    host_iv = bytes.fromhex("11223344")
    tail = bytes.fromhex("55667788")
    response_cipher = build_secure_session_response(request, KEY, host_iv, tail)
    response_plain = decrypt(response_cipher, KEY)
    assert response_plain[0] == 7
    assert response_plain[1:8] == bytes(7)
    assert response_plain[8:12] == host_iv
    assert response_plain[12:16] == tail
