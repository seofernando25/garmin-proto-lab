"""CRC primitive used by the reconstructed GFDI frame format.

Protocol fact: reflected CRC-16 using polynomial 0xA001 and initial value 0.
See P-0103 in ``spec/PROTOCOL.md``.
"""
from __future__ import annotations


def crc16_arc(data: bytes | bytearray | memoryview, initial: int = 0) -> int:
    """Return the unsigned 16-bit reflected CRC for *data*.

    With ``initial=0`` this is the CRC-16/ARC check used by the GFDI frame.
    """
    crc = initial & 0xFFFF
    for octet in data:
        crc ^= int(octet)
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF
