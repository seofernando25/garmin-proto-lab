"""Consistent Overhead Byte Stuffing for the GFDI byte stream."""
from __future__ import annotations


class CobsError(ValueError):
    pass


def encode(data: bytes) -> bytes:
    """Encode one COBS payload without the surrounding zero delimiters."""
    out = bytearray([0])
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(value)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1
    out[code_index] = code
    return bytes(out)


def decode(encoded: bytes) -> bytes:
    """Decode one delimiter-free COBS payload."""
    if not encoded:
        raise CobsError("empty COBS payload")
    if 0 in encoded:
        raise CobsError("zero byte inside COBS payload")
    out = bytearray()
    pos = 0
    size = len(encoded)
    while pos < size:
        code = encoded[pos]
        if code == 0:
            raise CobsError("zero byte inside COBS payload")
        pos += 1
        end = pos + code - 1
        if end > size:
            raise CobsError("COBS code extends beyond payload")
        out.extend(encoded[pos:end])
        pos = end
        if code != 0xFF and pos < size:
            out.append(0)
    return bytes(out)


def frame(data: bytes) -> bytes:
    """Encode and add the leading and trailing zero stream delimiters."""
    return b"\x00" + encode(data) + b"\x00"


class StreamDecoder:
    """Incrementally recover delimiter-framed COBS packets from BLE chunks.

    Garmin's BLE input stream concatenates characteristic notifications, so a
    COBS packet may span an arbitrary number of ATT notifications.
    """

    def __init__(self, max_encoded_size: int = 16384) -> None:
        if max_encoded_size <= 0:
            raise ValueError("max_encoded_size must be positive")
        self.max_encoded_size = max_encoded_size
        self._buf = bytearray()
        self._in_packet = False

    def feed(self, chunk: bytes) -> list[bytes]:
        packets: list[bytes] = []
        for value in chunk:
            if value == 0:
                if self._in_packet and self._buf:
                    encoded = bytes(self._buf)
                    # A delimiter is also our resynchronization point. Clear
                    # the failed packet before decoding so a malformed packet
                    # cannot poison the next packet after an exception.
                    self._buf.clear()
                    self._in_packet = True
                    packets.append(decode(encoded))
                else:
                    self._buf.clear()
                    self._in_packet = True
                continue
            if not self._in_packet:
                # Static evidence shows the reader scans until a zero delimiter.
                continue
            if len(self._buf) >= self.max_encoded_size:
                self._buf.clear()
                self._in_packet = False
                raise CobsError("encoded packet exceeds configured maximum")
            self._buf.append(value)
        return packets
