"""GFDI Configuration (5050) capability bitset helpers."""
from __future__ import annotations

from dataclasses import dataclass


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Configuration:
    flags: frozenset[int]
    raw_bitset: bytes
    trailing: bytes = b""

    def effective_flags(self) -> frozenset[int]:
        """Apply the legacy compatibility rule observed in HandshakeHandler.

        If capability 3 exists while 4 and 90 do not, capability 4 is treated
        as effective without mutating the raw peer bitset.
        """
        flags = set(self.flags)
        if 3 in flags and 4 not in flags and 90 not in flags:
            flags.add(4)
        return frozenset(flags)


def flags_from_bitset(bitset: bytes) -> frozenset[int]:
    flags: set[int] = set()
    for byte_index, value in enumerate(bitset):
        for bit in range(8):
            if value & (1 << bit):
                flags.add(byte_index * 8 + bit)
    return frozenset(flags)


def bitset_from_flags(flags: set[int] | frozenset[int]) -> bytes:
    if not flags:
        return b""
    for flag in flags:
        if flag < 0:
            raise ConfigurationError("configuration flag cannot be negative")
    largest = max(flags)
    out = bytearray(largest // 8 + 1)
    for flag in flags:
        out[flag // 8] |= 1 << (flag % 8)
    return bytes(out)


def parse_configuration(payload: bytes, *, allow_trailing: bool = True) -> Configuration:
    if not payload:
        raise ConfigurationError("5050 configuration payload is empty")
    count = payload[0]
    end = 1 + count
    if len(payload) < end:
        raise ConfigurationError("5050 configuration bitset is truncated")
    trailing = bytes(payload[end:])
    if trailing and not allow_trailing:
        raise ConfigurationError("trailing bytes after 5050 configuration bitset")
    raw = bytes(payload[1:end])
    return Configuration(flags_from_bitset(raw), raw, trailing)


def encode_configuration(flags: set[int] | frozenset[int]) -> bytes:
    raw = bitset_from_flags(flags)
    if len(raw) > 0xFF:
        raise ConfigurationError("5050 bitset exceeds one-byte length prefix")
    return bytes([len(raw)]) + raw
