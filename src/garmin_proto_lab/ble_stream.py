"""BLE byte-stream behavior below the COBS/GFDI layer."""
from __future__ import annotations


def split_att_writes(data: bytes, write_payload_size: int) -> list[bytes]:
    """Split stream bytes exactly as the BLE characteristic OutputStream does.

    There is no Garmin-specific per-fragment header at this layer; the remote
    input side concatenates notification payloads into a byte stream.
    """
    if write_payload_size <= 0:
        raise ValueError("write_payload_size must be positive")
    return [data[i:i + write_payload_size] for i in range(0, len(data), write_payload_size)]


class NotificationByteStream:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, notification: bytes) -> None:
        self._buffer.extend(notification)

    def read(self, size: int | None = None) -> bytes:
        if size is None or size >= len(self._buffer):
            out = bytes(self._buffer)
            self._buffer.clear()
            return out
        if size < 0:
            raise ValueError("size cannot be negative")
        out = bytes(self._buffer[:size])
        del self._buffer[:size]
        return out
