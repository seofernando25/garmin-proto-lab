"""Pure pairing/session cryptographic transforms inferred from static evidence.

The stateful Bluetooth/user-interaction orchestration is intentionally separate;
these functions are deterministic protocol facts suitable for unit testing.
"""
from __future__ import annotations

from .xxtea import encrypt as xxtea_encrypt


class PairingError(ValueError):
    pass



def passkey_from_decimal(text: str) -> bytes:
    """Convert the user-visible decimal passkey to Garmin's 16-byte key.

    The Android path parses a signed 32-bit decimal integer, emits that integer
    as four little-endian bytes and zero-pads the result to sixteen bytes.
    """
    try:
        value = int(text, 10)
    except ValueError as exc:
        raise PairingError("passkey must be a decimal integer") from exc
    if not -(1 << 31) <= value < (1 << 31):
        raise PairingError("passkey is outside signed 32-bit range")
    return value.to_bytes(4, "little", signed=True) + bytes(12)


def mac_block(mac: str) -> bytes:
    """Return the 16-byte pairing block containing the reversed six-byte MAC."""
    parts = mac.split(":")
    if len(parts) != 6:
        raise PairingError("MAC address must contain six colon-separated octets")
    try:
        raw = bytes(int(part, 16) for part in parts)
    except ValueError as exc:
        raise PairingError("invalid hexadecimal MAC address") from exc
    if any(len(part) != 2 for part in parts):
        raise PairingError("each MAC octet must have two hex digits")
    return raw[::-1] + bytes(10)


def confirm_value(mac: str, passkey: bytes, random16: bytes) -> bytes:
    """Compute the 16-byte STK confirm value (P-0206)."""
    if len(passkey) != 16 or len(random16) != 16:
        raise PairingError("passkey and random value must each be 16 bytes")
    first = xxtea_encrypt(random16, passkey)
    mixed = bytes(a ^ b ^ c for a, b, c in zip(first, mac_block(mac), random16, strict=True))
    return xxtea_encrypt(mixed, passkey)


def short_term_key(passkey: bytes, host_random16: bytes, device_random16: bytes) -> bytes:
    """Derive the STK from the first eight bytes of each confirmed random value."""
    if len(passkey) != 16 or len(host_random16) != 16 or len(device_random16) != 16:
        raise PairingError("passkey and random values must each be 16 bytes")
    seed = host_random16[:8] + device_random16[:8]
    return xxtea_encrypt(seed, passkey)


def session_key(long_term_key: bytes, device_skd8: bytes, host_skd8: bytes) -> bytes:
    """Derive the 16-byte session key from device||host SKD contributions."""
    if len(long_term_key) != 16 or len(device_skd8) != 8 or len(host_skd8) != 8:
        raise PairingError("LTK must be 16 bytes and SKD contributions eight bytes each")
    return xxtea_encrypt(device_skd8 + host_skd8, long_term_key)
