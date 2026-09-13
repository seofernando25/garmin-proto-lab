"""Authenticated-session packet wrapper reconstructed for GFDI."""
from __future__ import annotations

from dataclasses import dataclass

from .frame import FrameError
from .xxtea import decrypt as xxtea_decrypt, encrypt as xxtea_encrypt


class SecurePacketError(ValueError):
    pass


def _pad_body(inner: bytes, counter: int, iv4: bytes) -> bytes:
    if len(iv4) < 4:
        raise SecurePacketError("initialization vector must contain at least four bytes")
    if not 0 <= counter <= 0xFFFFFFFF:
        raise SecurePacketError("packet counter out of uint32 range")
    prefix = counter.to_bytes(4, "little") + iv4[:4]
    used = len(prefix) + len(inner)
    padded = used + (4 - (used % 4))
    return prefix + inner + bytes(padded - used)


def encrypt_packet(inner_frame: bytes, session_key: bytes, host_iv: bytes, counter: int) -> bytes:
    body = _pad_body(inner_frame, counter, host_iv)
    encrypted = xxtea_encrypt(body, session_key)
    total = len(encrypted) + 2
    if total > 0xFFFF:
        raise SecurePacketError("encrypted packet exceeds uint16 length")
    return total.to_bytes(2, "little") + encrypted


@dataclass(slots=True)
class SecurePacketDecoder:
    session_key: bytes
    device_iv: bytes
    last_counter: int = 0
    seen_packet: bool = False

    def decrypt(self, packet: bytes) -> bytes:
        if len(packet) < 10:
            raise SecurePacketError("encrypted packet too short")
        declared = int.from_bytes(packet[:2], "little")
        if declared != len(packet):
            raise SecurePacketError("encrypted packet length mismatch")
        ciphertext = packet[2:]
        if len(ciphertext) % 4:
            raise SecurePacketError("encrypted payload is not word aligned")
        body = xxtea_decrypt(ciphertext, self.session_key)
        if len(body) < 14:
            raise SecurePacketError("decrypted body too short")
        counter = int.from_bytes(body[:4], "little")
        if self.seen_packet and counter < self.last_counter:
            raise SecurePacketError("packet counter moved backwards")
        if body[4:8] != self.device_iv[:4]:
            raise SecurePacketError("device initialization vector mismatch")
        inner_len = int.from_bytes(body[8:10], "little")
        end = 8 + inner_len
        if inner_len < 6 or end > len(body):
            raise SecurePacketError("inner frame length exceeds decrypted body")
        self.last_counter = counter
        self.seen_packet = True
        return bytes(body[8:end])
