"""GFDI plaintext frame grammar reconstructed from static evidence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .crc import crc16_arc

ACK_MESSAGE_ID = 5000
TRANSACTION_BASE = 5000
MAX_FRAME_LENGTH = 0xFFFF


class FrameError(ValueError):
    pass


class ResponseStatus(IntEnum):
    ACK = 0
    NAK = 1
    UNKNOWN_OR_NOT_SUPPORTED = 2
    COBS_DECODER_ERROR = 3
    CRC_ERROR = 4
    LENGTH_ERROR = 5


@dataclass(frozen=True, slots=True)
class Frame:
    message_type: int
    payload: bytes = b""
    transaction_id: int | None = None

    @property
    def is_response(self) -> bool:
        return self.message_type == ACK_MESSAGE_ID


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    request_type: int
    status: ResponseStatus | int
    payload: bytes
    transaction_id: int | None = None


def encode_frame(frame: Frame) -> bytes:
    if not 0 <= frame.message_type <= 0xFFFF:
        raise FrameError("message_type out of uint16 range")
    if frame.transaction_id is None and frame.message_type > 0x7FFF:
        raise FrameError("ordinary framing reserves bit 15 for transaction mode")
    header = bytearray(b"\x00\x00")
    if frame.transaction_id is None:
        header += frame.message_type.to_bytes(2, "little")
    else:
        if not 0 <= frame.transaction_id <= 31:
            raise FrameError("transaction_id must be 0..31")
        compact = frame.message_type - TRANSACTION_BASE
        if not 0 <= compact <= 0xFF:
            raise FrameError("compact transaction framing requires message type 5000..5255")
        header.append(compact)
        header.append(0x80 | frame.transaction_id)
    body = header + frame.payload
    total = len(body) + 2
    if total > MAX_FRAME_LENGTH:
        raise FrameError("frame exceeds uint16 length")
    body[0:2] = total.to_bytes(2, "little")
    crc = crc16_arc(body)
    return bytes(body) + crc.to_bytes(2, "little")


def decode_frame(raw: bytes) -> Frame:
    if len(raw) < 6:
        raise FrameError("frame shorter than six-byte minimum")
    declared = int.from_bytes(raw[0:2], "little")
    if declared != len(raw):
        raise FrameError(f"length mismatch: declared {declared}, actual {len(raw)}")
    expected = int.from_bytes(raw[-2:], "little")
    actual = crc16_arc(raw[:-2])
    if expected != actual:
        raise FrameError(f"CRC mismatch: expected 0x{expected:04x}, calculated 0x{actual:04x}")
    marker = raw[3]
    if marker & 0x80:
        transaction_id = marker & 0x1F
        message_type = TRANSACTION_BASE + raw[2]
    else:
        transaction_id = None
        message_type = int.from_bytes(raw[2:4], "little")
    return Frame(message_type=message_type, payload=bytes(raw[4:-2]), transaction_id=transaction_id)


def encode_ack(request_type: int, status: ResponseStatus | int = ResponseStatus.ACK,
               payload: bytes = b"", transaction_id: int | None = None) -> bytes:
    status_value = int(status)
    if not 0 <= status_value <= 0xFF:
        raise FrameError("response status out of byte range")
    ack_payload = request_type.to_bytes(2, "little") + bytes([status_value]) + payload
    return encode_frame(Frame(ACK_MESSAGE_ID, ack_payload, transaction_id))


def parse_ack(frame: Frame) -> Acknowledgement:
    if not frame.is_response:
        raise FrameError("not an acknowledgement frame")
    if len(frame.payload) < 3:
        raise FrameError("acknowledgement payload shorter than three bytes")
    request_type = int.from_bytes(frame.payload[0:2], "little")
    raw_status = frame.payload[2]
    try:
        status: ResponseStatus | int = ResponseStatus(raw_status)
    except ValueError:
        status = raw_status
    return Acknowledgement(request_type, status, frame.payload[3:], frame.transaction_id)
