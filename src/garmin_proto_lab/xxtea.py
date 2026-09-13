"""Independent XXTEA primitive used by Garmin's GFDI authentication path.

This is a protocol primitive implementation, not decompiler output.  Words are
32-bit unsigned little-endian and the standard XXTEA delta is 0x9E3779B9.
"""
from __future__ import annotations

DELTA = 0x9E3779B9
MASK = 0xFFFFFFFF


class XxteaError(ValueError):
    pass


def _words(data: bytes) -> list[int]:
    if len(data) % 4:
        raise XxteaError("XXTEA data length must be a multiple of four")
    return [int.from_bytes(data[i:i+4], "little") for i in range(0, len(data), 4)]


def _key_words(key: bytes) -> list[int]:
    if len(key) != 16:
        raise XxteaError("GFDI XXTEA key must be exactly 16 bytes")
    return _words(key)


def _mx(sum_: int, y: int, z: int, p: int, e: int, key: list[int]) -> int:
    return ((((z >> 5) ^ ((y << 2) & MASK)) + ((y >> 3) ^ ((z << 4) & MASK))) ^
            (((sum_ ^ y) + (key[(p & 3) ^ e] ^ z)))) & MASK


def encrypt(data: bytes, key: bytes) -> bytes:
    v = _words(data)
    k = _key_words(key)
    n = len(v)
    if n < 2:
        raise XxteaError("XXTEA requires at least two 32-bit words")
    rounds = 6 + 52 // n
    total = 0
    z = v[-1]
    for _ in range(rounds):
        total = (total + DELTA) & MASK
        e = (total >> 2) & 3
        for p in range(n - 1):
            y = v[p + 1]
            v[p] = (v[p] + _mx(total, y, z, p, e, k)) & MASK
            z = v[p]
        y = v[0]
        v[-1] = (v[-1] + _mx(total, y, z, n - 1, e, k)) & MASK
        z = v[-1]
    return b"".join(x.to_bytes(4, "little") for x in v)


def decrypt(data: bytes, key: bytes) -> bytes:
    v = _words(data)
    k = _key_words(key)
    n = len(v)
    if n < 2:
        raise XxteaError("XXTEA requires at least two 32-bit words")
    rounds = 6 + 52 // n
    total = (rounds * DELTA) & MASK
    y = v[0]
    while total:
        e = (total >> 2) & 3
        for p in range(n - 1, 0, -1):
            z = v[p - 1]
            v[p] = (v[p] - _mx(total, y, z, p, e, k)) & MASK
            y = v[p]
        z = v[-1]
        v[0] = (v[0] - _mx(total, y, z, 0, e, k)) & MASK
        y = v[0]
        total = (total - DELTA) & MASK
    return b"".join(x.to_bytes(4, "little") for x in v)


def encrypt_padded(data: bytes, key: bytes) -> bytes:
    """Encrypt using the self-describing padding used by the GNCS data path.

    The first plaintext byte stores the number of trailing padding bytes.  The
    whole padded plaintext is word aligned.  Garmin's transform deliberately
    uses five bytes of overhead for a payload whose length is 3 mod 4, so the
    first byte plus trailing padding never creates an ambiguous one-byte pad.
    """
    if not data:
        return b""
    missing = 4 - (len(data) % 4)
    overhead = 5 if missing == 1 else missing
    # For an already aligned payload, ``missing`` is four.
    padded = bytes([overhead - 1]) + data + bytes(overhead - 1)
    return encrypt(padded, key)


def decrypt_padded(ciphertext: bytes, key: bytes) -> bytes:
    """Inverse of :func:`encrypt_padded` with bounds validation."""
    if not ciphertext:
        return b""
    plain = decrypt(ciphertext, key)
    padding = plain[0]
    if padding + 1 > len(plain):
        raise XxteaError("invalid padded XXTEA length marker")
    return bytes(plain[1 : len(plain) - padding])
