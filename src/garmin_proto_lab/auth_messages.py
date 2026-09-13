"""Pure codecs for the GFDI authentication/session message family.

This module deliberately contains no BLE I/O and no secret persistence.  It is
an independent implementation of the byte layouts documented as P-0201..
P-0211 in ``spec/PROTOCOL.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .xxtea import decrypt as xxtea_decrypt, encrypt as xxtea_encrypt

XXTEA_ALGORITHM_MASK = 0x00000001


class AuthCodecError(ValueError):
    pass


class PasskeyMode(IntEnum):
    VISIBLE = 0
    JUST_WORKS = 1
    OUT_OF_BAND = 2


@dataclass(frozen=True, slots=True)
class NegotiationRequest:
    prefix: int
    algorithm_mask: int

    @classmethod
    def parse(cls, payload: bytes) -> "NegotiationRequest":
        if len(payload) < 5:
            raise AuthCodecError("5101 negotiation request shorter than five bytes")
        return cls(payload[0], int.from_bytes(payload[1:5], "little"))

    def encode(self) -> bytes:
        if not 0 <= self.prefix <= 0xFF:
            raise AuthCodecError("negotiation prefix out of byte range")
        if not 0 <= self.algorithm_mask <= 0xFFFFFFFF:
            raise AuthCodecError("algorithm mask out of uint32 range")
        return bytes([self.prefix]) + self.algorithm_mask.to_bytes(4, "little")


@dataclass(frozen=True, slots=True)
class NegotiationResponse:
    common_algorithm_status: int
    has_persistent_ltk: bool
    algorithm_mask: int = XXTEA_ALGORITHM_MASK

    def encode(self) -> bytes:
        if not 0 <= self.common_algorithm_status <= 0xFF:
            raise AuthCodecError("negotiation status out of byte range")
        if not 0 <= self.algorithm_mask <= 0xFFFFFFFF:
            raise AuthCodecError("algorithm mask out of uint32 range")
        return (
            bytes([self.common_algorithm_status, int(self.has_persistent_ltk)])
            + self.algorithm_mask.to_bytes(4, "little")
        )

    @classmethod
    def from_request(cls, request: NegotiationRequest, has_persistent_ltk: bool) -> "NegotiationResponse":
        common = request.algorithm_mask & XXTEA_ALGORITHM_MASK
        return cls(0 if common else 1, has_persistent_ltk, XXTEA_ALGORITHM_MASK)


@dataclass(frozen=True, slots=True)
class StkBeginRequest:
    prefix: int
    timeout_seconds: int
    unknown_3_4: bytes
    mode: PasskeyMode | int

    @classmethod
    def parse(cls, payload: bytes) -> "StkBeginRequest":
        if len(payload) < 6:
            raise AuthCodecError("5103 STK begin request shorter than six bytes")
        raw_mode = payload[5]
        try:
            mode: PasskeyMode | int = PasskeyMode(raw_mode)
        except ValueError:
            mode = raw_mode
        return cls(
            prefix=payload[0],
            timeout_seconds=int.from_bytes(payload[1:3], "little"),
            unknown_3_4=bytes(payload[3:5]),
            mode=mode,
        )

    @property
    def effective_timeout_seconds(self) -> int:
        return self.timeout_seconds or 30


@dataclass(frozen=True, slots=True)
class StkBeginResponse:
    status: int
    mode_or_sentinel: int

    @classmethod
    def for_request(cls, request: StkBeginRequest) -> "StkBeginResponse":
        raw_mode = int(request.mode)
        if raw_mode in (0, 1, 2):
            return cls(0, 0xFF)
        return cls(0, raw_mode)

    def encode(self) -> bytes:
        if not 0 <= self.status <= 0xFF or not 0 <= self.mode_or_sentinel <= 0xFF:
            raise AuthCodecError("STK begin response byte out of range")
        return bytes([self.status, self.mode_or_sentinel])


@dataclass(frozen=True, slots=True)
class PeerValueResponse:
    status: int
    value16: bytes | None

    @classmethod
    def parse(cls, payload: bytes, *, label: str) -> "PeerValueResponse":
        if not payload:
            raise AuthCodecError(f"{label} response is empty")
        status = payload[0]
        if status != 0:
            return cls(status, None)
        if len(payload) < 17:
            raise AuthCodecError(f"successful {label} response shorter than 17 bytes")
        return cls(status, bytes(payload[1:17]))


def build_confirm_request(confirm16: bytes) -> bytes:
    if len(confirm16) != 16:
        raise AuthCodecError("5104 confirm value must be 16 bytes")
    return bytes(confirm16)


def parse_confirm_response(payload: bytes) -> PeerValueResponse:
    return PeerValueResponse.parse(payload, label="5104 confirm")


def build_random_request(random16: bytes) -> bytes:
    if len(random16) != 16:
        raise AuthCodecError("5105 random value must be 16 bytes")
    return bytes(random16)


def parse_random_response(payload: bytes) -> PeerValueResponse:
    # Static special case: status 2 means peer confirm verification failed and
    # authentication is restarted.  This parser preserves the numeric status.
    return PeerValueResponse.parse(payload, label="5105 random")


def build_stk_generation_status(success: bool) -> bytes:
    return bytes([0 if success else 1])


def build_ltk_reconnect_request(encrypted_diversifier: bytes, random_number: bytes) -> bytes:
    if len(encrypted_diversifier) != 2:
        raise AuthCodecError("EDIV must be exactly two bytes")
    if len(random_number) != 8:
        raise AuthCodecError("RAND must be exactly eight bytes")
    return bytes(encrypted_diversifier + random_number)


@dataclass(frozen=True, slots=True)
class LongTermMaterial:
    ignored_prefix: int
    long_term_key: bytes
    encrypted_diversifier: bytes
    random_number: bytes
    reserved: bytes


def decrypt_ltk_distribution(payload: bytes, short_term_key: bytes) -> LongTermMaterial:
    if len(payload) < 33:
        raise AuthCodecError("5107 LTK distribution shorter than 33 bytes")
    cipher = payload[1:33]
    plain = xxtea_decrypt(cipher, short_term_key)
    return LongTermMaterial(
        ignored_prefix=payload[0],
        long_term_key=bytes(plain[0:16]),
        encrypted_diversifier=bytes(plain[16:18]),
        random_number=bytes(plain[18:26]),
        reserved=bytes(plain[26:32]),
    )


def build_ltk_distribution_response(success: bool) -> bytes:
    return bytes([0 if success else 1])


@dataclass(frozen=True, slots=True)
class SessionKeyExchange:
    device_skd8: bytes

    @classmethod
    def parse_request(cls, payload: bytes) -> "SessionKeyExchange":
        if len(payload) < 8:
            raise AuthCodecError("5108 session-key distribution shorter than eight bytes")
        return cls(bytes(payload[:8]))

    def build_response(self, host_skd8: bytes) -> bytes:
        if len(host_skd8) != 8:
            raise AuthCodecError("host SKD must be exactly eight bytes")
        return bytes([0]) + host_skd8


def derive_session_key(long_term_key: bytes, device_skd8: bytes, host_skd8: bytes) -> bytes:
    if len(long_term_key) != 16 or len(device_skd8) != 8 or len(host_skd8) != 8:
        raise AuthCodecError("LTK must be 16 bytes and each SKD contribution eight bytes")
    return xxtea_encrypt(device_skd8 + host_skd8, long_term_key)


@dataclass(frozen=True, slots=True)
class SessionVerification:
    ignored_prefix: int
    echoed_session_key: bytes
    unknown_tail8: bytes


def verify_session_key_message(payload: bytes, session_key: bytes) -> tuple[SessionVerification, bool]:
    if len(payload) < 25:
        raise AuthCodecError("5109 session verification shorter than 25 bytes")
    if len(session_key) != 16:
        raise AuthCodecError("session key must be 16 bytes")
    plain = xxtea_decrypt(payload[1:25], session_key)
    parsed = SessionVerification(payload[0], bytes(plain[:16]), bytes(plain[16:24]))
    return parsed, parsed.echoed_session_key == session_key


def build_session_verification_response(matches: bool, has_session_key: bool = True) -> bytes:
    if not has_session_key:
        return bytes([1])
    return bytes([0 if matches else 2])


@dataclass(frozen=True, slots=True)
class SecureSessionRequest:
    selector: int
    unknown_1_7: bytes
    device_iv4: bytes
    unknown_12_15: bytes


def decrypt_secure_session_request(payload: bytes, session_key: bytes) -> SecureSessionRequest:
    if len(payload) < 16:
        raise AuthCodecError("5111 secure-session request shorter than 16 bytes")
    plain = xxtea_decrypt(payload[:16], session_key)
    return SecureSessionRequest(
        selector=plain[0],
        unknown_1_7=bytes(plain[1:8]),
        device_iv4=bytes(plain[8:12]),
        unknown_12_15=bytes(plain[12:16]),
    )


def build_secure_session_response(
    request: SecureSessionRequest,
    session_key: bytes,
    host_iv4: bytes,
    random_tail4: bytes,
) -> bytes:
    if len(host_iv4) != 4 or len(random_tail4) != 4:
        raise AuthCodecError("5111 host IV and random tail must each be four bytes")
    plain = bytearray(16)
    plain[0] = request.selector
    plain[8:12] = host_iv4
    plain[12:16] = random_tail4
    return xxtea_encrypt(bytes(plain), session_key)
