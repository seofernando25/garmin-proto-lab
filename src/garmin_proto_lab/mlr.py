"""Clean-room codec/state for Garmin MultiLink Reliable (MLR) packets.

The packet format is recovered from ``libreliable-ml.so`` plus the Java JNI
wrapper. This module intentionally implements only deterministic wire behavior
needed by the FileAccess transport pipe. Timing/window tuning from the native
ARQ engine remains documented separately and is not guessed here.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


class MlrError(ValueError):
    pass


WIRE_SEQUENCE_MODULUS = 64
RELIABLE_HANDLE_MIN = 0x80
RELIABLE_HANDLE_MAX = 0x87


@dataclass(frozen=True, slots=True)
class MlrPacket:
    """One raw MultiLink packet.

    Non-reliable packets have a one-byte handle. Reliable packets use a two-byte
    header containing a reliable handle (0x80..0x87), six-bit sequence number
    and six-bit cumulative request/ACK number.
    """

    handle: int
    payload: bytes = b""
    reliable: bool = False
    sequence_number: int = 0
    request_number: int = 0

    def encode(self) -> bytes:
        payload = bytes(self.payload)
        if self.reliable:
            if not RELIABLE_HANDLE_MIN <= self.handle <= RELIABLE_HANDLE_MAX:
                raise MlrError("reliable handle must be in 0x80..0x87")
            if not 0 <= self.sequence_number < WIRE_SEQUENCE_MODULUS:
                raise MlrError("sequence number must fit six bits")
            if not 0 <= self.request_number < WIRE_SEQUENCE_MODULUS:
                raise MlrError("request number must fit six bits")
            first = 0x80 | ((self.handle & 0x07) << 4) | ((self.request_number >> 2) & 0x0F)
            second = ((self.request_number & 0x03) << 6) | (self.sequence_number & 0x3F)
            return bytes((first, second)) + payload
        if not 0 <= self.handle < 0x80:
            raise MlrError("non-reliable handle must fit seven bits")
        if self.sequence_number or self.request_number:
            raise MlrError("non-reliable packet cannot carry sequence/request numbers")
        return bytes((self.handle,)) + payload

    @classmethod
    def parse(cls, data: bytes) -> "MlrPacket":
        raw = bytes(data)
        if not raw:
            raise MlrError("MLR packet is empty")
        first = raw[0]
        if first & 0x80 == 0:
            return cls(first, raw[1:], False, 0, 0)
        if len(raw) < 2:
            raise MlrError("reliable MLR packet is missing its second header byte")
        second = raw[1]
        handle = RELIABLE_HANDLE_MIN | ((first & 0x70) >> 4)
        request_number = ((first & 0x0F) << 2) | (second >> 6)
        sequence_number = second & 0x3F
        return cls(handle, raw[2:], True, sequence_number, request_number)


def packets_required(data_length: int, max_write_length: int) -> int:
    """Return native MLR's packet count for a blob at a given write length."""
    if data_length < 0:
        raise MlrError("data length cannot be negative")
    if max_write_length <= 2:
        raise MlrError("reliable MLR write length must exceed two header bytes")
    return (data_length + max_write_length - 3) // (max_write_length - 2)


def fragment_blob(
    handle: int,
    payload: bytes,
    max_write_length: int,
    *,
    start_sequence: int = 0,
    request_number: int = 0,
) -> tuple[MlrPacket, ...]:
    """Fragment one reliable service blob into raw-packet payloads."""
    if max_write_length <= 2:
        raise MlrError("reliable MLR write length must exceed two header bytes")
    if not 0 <= start_sequence < WIRE_SEQUENCE_MODULUS:
        raise MlrError("start sequence must fit six bits")
    if not 0 <= request_number < WIRE_SEQUENCE_MODULUS:
        raise MlrError("request number must fit six bits")
    chunk_size = max_write_length - 2
    raw = bytes(payload)
    out: list[MlrPacket] = []
    sequence = start_sequence
    for offset in range(0, len(raw), chunk_size):
        out.append(MlrPacket(handle, raw[offset : offset + chunk_size], True, sequence, request_number))
        sequence = (sequence + 1) % WIRE_SEQUENCE_MODULUS
    return tuple(out)


@dataclass(frozen=True, slots=True)
class MlrReceiveResult:
    data: bytes | None
    acknowledgement: bytes | None
    newly_acked: int
    duplicate_or_out_of_order: bool


class ReliableMlrSession:
    """Bounded cumulative-ACK state for FileAccess bring-up.

    Garmin's native engine has adaptive windows/RTOs. This clean-room subset is
    intentionally conservative: it supports a bounded sender (notably the
    10-byte transport-pipe configure command), accepts in-order peer data, and
    ACKs every data packet immediately. Native timing policy remains unresolved.
    """

    def __init__(self, handle: int, max_write_length: int, *, max_outstanding: int = 31) -> None:
        if not RELIABLE_HANDLE_MIN <= handle <= RELIABLE_HANDLE_MAX:
            raise MlrError("reliable session handle must be in 0x80..0x87")
        if max_write_length <= 2:
            raise MlrError("max write length must exceed two MLR header bytes")
        if not 1 <= max_outstanding < WIRE_SEQUENCE_MODULUS:
            raise MlrError("max_outstanding must be between 1 and 63")
        self.handle = handle
        self.max_write_length = max_write_length
        self.max_outstanding = max_outstanding
        self.send_next = 0
        self.receive_next = 0
        self._outstanding: OrderedDict[int, bytes] = OrderedDict()

    @property
    def outstanding_count(self) -> int:
        return len(self._outstanding)

    def send_blob(self, payload: bytes) -> tuple[bytes, ...]:
        packets = fragment_blob(
            self.handle,
            bytes(payload),
            self.max_write_length,
            start_sequence=self.send_next,
            request_number=self.receive_next,
        )
        if len(self._outstanding) + len(packets) > self.max_outstanding:
            raise MlrError("MLR send window would exceed configured outstanding bound")
        encoded: list[bytes] = []
        for packet in packets:
            if packet.sequence_number in self._outstanding:
                raise MlrError("MLR sequence number wrapped while still outstanding")
            self._outstanding[packet.sequence_number] = packet.payload
            encoded.append(packet.encode())
            self.send_next = (packet.sequence_number + 1) % WIRE_SEQUENCE_MODULUS
        return tuple(encoded)

    def _apply_cumulative_ack(self, request_number: int) -> int:
        if not self._outstanding:
            return 0
        oldest = next(iter(self._outstanding))
        distance = (request_number - oldest) % WIRE_SEQUENCE_MODULUS
        if distance == 0 or distance > len(self._outstanding):
            return 0
        for _ in range(distance):
            self._outstanding.popitem(last=False)
        return distance

    def receive(self, raw_packet: bytes) -> MlrReceiveResult:
        packet = MlrPacket.parse(raw_packet)
        if not packet.reliable:
            raise MlrError("reliable session received a non-reliable packet")
        if packet.handle != self.handle:
            raise MlrError(
                f"packet handle 0x{packet.handle:02x} does not match session 0x{self.handle:02x}"
            )
        newly_acked = self._apply_cumulative_ack(packet.request_number)
        if not packet.payload:
            return MlrReceiveResult(None, None, newly_acked, False)

        duplicate_or_out_of_order = packet.sequence_number != self.receive_next
        data: bytes | None = None
        if not duplicate_or_out_of_order:
            data = packet.payload
            self.receive_next = (self.receive_next + 1) % WIRE_SEQUENCE_MODULUS

        ack = MlrPacket(
            self.handle,
            b"",
            True,
            sequence_number=0,
            request_number=self.receive_next,
        ).encode()
        return MlrReceiveResult(data, ack, newly_acked, duplicate_or_out_of_order)

    def retransmit_outstanding(self) -> tuple[bytes, ...]:
        return tuple(
            MlrPacket(
                self.handle,
                payload,
                True,
                sequence_number=sequence,
                request_number=self.receive_next,
            ).encode()
            for sequence, payload in self._outstanding.items()
        )
