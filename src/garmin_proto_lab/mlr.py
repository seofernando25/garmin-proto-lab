"""Garmin MultiLink Reliable (MLR) packet codec and ARQ state machine.

The wire layout, sequence arithmetic, receive ACK policy, congestion window and
retransmission timing are reconstructed from ``libreliable-ml.so`` and its JNI
bridge. The implementation is platform-independent and contains no native
Garmin dependency.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Callable


class MlrError(ValueError):
    pass


WIRE_SEQUENCE_MODULUS = 64
RELIABLE_HANDLE_MIN = 0x80
RELIABLE_HANDLE_MAX = 0x87
INITIAL_WINDOW_PACKETS = 32
MAX_WINDOW_PACKETS = 63
INITIAL_RTO_MS = 1000
MIN_RTO_MS = 500
MAX_BACKOFF_RTO_MS = 20_000
DEFERRED_ACK_MS = 10
ACK_PACKET_THRESHOLD = 5
RTT_ALPHA = 1.0 / 8.0
RTT_BETA = 1.0 / 4.0
RTT_VARIANCE_MULTIPLIER = 4.0
RTT_RELATIVE_FLOOR = 0.5


def _default_clock_ms() -> float:
    return time.monotonic() * 1000.0


def sequence_number_count(mode: int = 0) -> int:
    """Return the native format's sequence-number count for its mode byte.

    Normal Java/JNI connections pass mode 0 and therefore use 64 values. The
    native formatter also exposes its diagnostic modes: 0xFF selects 8 and any
    other non-zero value returns 0xFF.
    """
    if not 0 <= mode <= 0xFF:
        raise MlrError("sequence-number mode must fit uint8")
    if mode == 0:
        return 64
    if mode == 0xFF:
        return 8
    return 0xFF


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
    """Return MLR's packet count for a blob at a given raw write length."""
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
    """Fragment one reliable service blob into MLR packets."""
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


@dataclass(slots=True)
class MlrRttEstimator:
    """Native-equivalent RTT estimator and RTO backoff state."""

    srtt_ms: float | None = None
    rttvar_ms: float | None = None
    rto_ms: int = INITIAL_RTO_MS
    last_update_ms: float | None = None

    def observe(self, sample_ms: float, now_ms: float) -> int:
        sample = max(0.0, float(sample_ms))
        if self.srtt_ms is None or self.rttvar_ms is None:
            self.srtt_ms = sample
            self.rttvar_ms = sample * RTT_RELATIVE_FLOOR
        else:
            # The native implementation scales alpha/beta when another RTT
            # update arrives sooner than one sample interval.
            ratio = 1.0
            if sample > 0 and self.last_update_ms is not None:
                ratio = min(1.0, max(0.0, now_ms - self.last_update_ms) / sample)
            alpha = RTT_ALPHA * ratio
            beta = RTT_BETA * ratio
            previous_srtt = self.srtt_ms
            self.rttvar_ms = (
                beta * abs(previous_srtt - sample)
                + (1.0 - beta) * self.rttvar_ms
            )
            self.srtt_ms = alpha * sample + (1.0 - alpha) * previous_srtt
        variance_term = RTT_VARIANCE_MULTIPLIER * self.rttvar_ms
        relative_term = RTT_RELATIVE_FLOOR * self.srtt_ms
        computed = int(self.srtt_ms + max(relative_term, variance_term) + 0.5)
        self.rto_ms = max(MIN_RTO_MS, computed)
        self.last_update_ms = now_ms
        return self.rto_ms

    def backoff(self) -> int:
        self.rto_ms = min(MAX_BACKOFF_RTO_MS, self.rto_ms * 2)
        return self.rto_ms


@dataclass(frozen=True, slots=True)
class MlrReceiveResult:
    data: bytes | None
    acknowledgement: bytes | None
    newly_acked: int
    duplicate_or_out_of_order: bool


@dataclass(slots=True)
class _OutstandingPacket:
    payload: bytes
    sent_at_ms: float
    boundary: int
    transmissions: int = 1


class ReliableMlrSession:
    """State machine for Garmin's 64-value cumulative-ACK reliable channel.

    The sender starts with a 32-packet window, increases the window by one after
    an advancing ACK up to 63, halves it on retransmission timeout, starts with
    a one-second RTO, applies the recovered RTT estimator, doubles timeout RTO
    up to 20 seconds, and requests an ACK after five received data packets or a
    10 ms deferred-ACK timer.
    """

    def __init__(
        self,
        handle: int,
        max_write_length: int,
        *,
        max_outstanding: int = MAX_WINDOW_PACKETS,
        clock_ms: Callable[[], float] = _default_clock_ms,
    ) -> None:
        if not RELIABLE_HANDLE_MIN <= handle <= RELIABLE_HANDLE_MAX:
            raise MlrError("reliable session handle must be in 0x80..0x87")
        if max_write_length <= 2:
            raise MlrError("max write length must exceed two MLR header bytes")
        if not 1 <= max_outstanding < WIRE_SEQUENCE_MODULUS:
            raise MlrError("max_outstanding must be between 1 and 63")
        self.handle = handle
        self.max_write_length = max_write_length
        self.max_outstanding = max_outstanding
        self.clock_ms = clock_ms
        self.send_next = 0
        self.receive_next = 0
        self.send_window = min(INITIAL_WINDOW_PACKETS, max_outstanding)
        self.rtt = MlrRttEstimator()
        self.timeout_count = 0
        self._outstanding: OrderedDict[int, _OutstandingPacket] = OrderedDict()
        self._boundary_sent_ms: dict[int, float] = {}
        self._ack_pending_packets = 0
        self._ack_deadline_ms: float | None = None

    @property
    def outstanding_count(self) -> int:
        return len(self._outstanding)

    @property
    def ack_pending_packets(self) -> int:
        return self._ack_pending_packets

    @property
    def ack_deadline_ms(self) -> float | None:
        return self._ack_deadline_ms

    @property
    def retransmit_deadline_ms(self) -> float | None:
        if not self._outstanding:
            return None
        oldest = next(iter(self._outstanding.values()))
        return oldest.sent_at_ms + self.rtt.rto_ms

    @property
    def available_send_slots(self) -> int:
        return max(0, min(self.send_window, self.max_outstanding) - self.outstanding_count)

    def _now(self, now_ms: float | None) -> float:
        return self.clock_ms() if now_ms is None else float(now_ms)

    def _clear_ack_pending(self) -> None:
        self._ack_pending_packets = 0
        self._ack_deadline_ms = None

    def _make_ack(self) -> bytes:
        self._clear_ack_pending()
        return MlrPacket(
            self.handle,
            b"",
            True,
            sequence_number=0,
            request_number=self.receive_next,
        ).encode()

    def send_blob(self, payload: bytes, *, now_ms: float | None = None) -> tuple[bytes, ...]:
        now = self._now(now_ms)
        packets = fragment_blob(
            self.handle,
            bytes(payload),
            self.max_write_length,
            start_sequence=self.send_next,
            request_number=self.receive_next,
        )
        if len(packets) > self.available_send_slots:
            raise MlrError(
                f"MLR send window has {self.available_send_slots} slot(s), blob requires {len(packets)}"
            )
        encoded: list[bytes] = []
        for packet in packets:
            if packet.sequence_number in self._outstanding:
                raise MlrError("MLR sequence number wrapped while still outstanding")
            boundary = (packet.sequence_number + 1) % WIRE_SEQUENCE_MODULUS
            self._outstanding[packet.sequence_number] = _OutstandingPacket(
                packet.payload,
                now,
                boundary,
            )
            self._boundary_sent_ms[boundary] = now
            encoded.append(packet.encode())
            self.send_next = boundary
        # Any outbound reliable packet carries the current RN and therefore
        # satisfies a pending receive acknowledgement.
        if encoded:
            self._clear_ack_pending()
        return tuple(encoded)

    def _apply_cumulative_ack(self, request_number: int, now_ms: float) -> int:
        if not self._outstanding:
            return 0
        oldest_sequence = next(iter(self._outstanding))
        distance = (request_number - oldest_sequence) % WIRE_SEQUENCE_MODULUS
        if distance == 0 or distance > len(self._outstanding):
            return 0
        for _ in range(distance):
            self._outstanding.popitem(last=False)
        sent = self._boundary_sent_ms.get(request_number)
        if sent is not None and now_ms >= sent:
            self.rtt.observe(now_ms - sent, now_ms)
        # The native window grows once per advancing ACK, not once per byte or
        # service blob.
        self.send_window = min(MAX_WINDOW_PACKETS, self.max_outstanding, self.send_window + 1)
        # Discard sequence-boundary timestamps that are no longer useful.
        active_boundaries = {record.boundary for record in self._outstanding.values()}
        for boundary in tuple(self._boundary_sent_ms):
            if boundary not in active_boundaries:
                self._boundary_sent_ms.pop(boundary, None)
        return distance

    def receive(self, raw_packet: bytes, *, now_ms: float | None = None) -> MlrReceiveResult:
        now = self._now(now_ms)
        packet = MlrPacket.parse(raw_packet)
        if not packet.reliable:
            raise MlrError("reliable session received a non-reliable packet")
        if packet.handle != self.handle:
            raise MlrError(
                f"packet handle 0x{packet.handle:02x} does not match session 0x{self.handle:02x}"
            )
        newly_acked = self._apply_cumulative_ack(packet.request_number, now)
        if not packet.payload:
            return MlrReceiveResult(None, None, newly_acked, False)

        duplicate_or_out_of_order = packet.sequence_number != self.receive_next
        data: bytes | None = None
        if not duplicate_or_out_of_order:
            data = packet.payload
            self.receive_next = (self.receive_next + 1) % WIRE_SEQUENCE_MODULUS

        if self._ack_pending_packets == 0:
            self._ack_deadline_ms = now + DEFERRED_ACK_MS
        self._ack_pending_packets += 1
        acknowledgement = None
        if self._ack_pending_packets >= ACK_PACKET_THRESHOLD:
            acknowledgement = self._make_ack()
        return MlrReceiveResult(data, acknowledgement, newly_acked, duplicate_or_out_of_order)

    def poll_ack(self, *, now_ms: float | None = None) -> bytes | None:
        """Return a deferred cumulative ACK when the native 10 ms timer expires."""
        now = self._now(now_ms)
        if (
            self._ack_pending_packets
            and self._ack_deadline_ms is not None
            and now >= self._ack_deadline_ms
        ):
            return self._make_ack()
        return None

    def force_ack(self) -> bytes | None:
        """Flush a pending cumulative ACK before closing or changing phases."""
        if not self._ack_pending_packets:
            return None
        return self._make_ack()

    def retransmit_outstanding(
        self,
        *,
        now_ms: float | None = None,
        limit: int | None = None,
    ) -> tuple[bytes, ...]:
        now = self._now(now_ms)
        count = self.outstanding_count if limit is None else max(0, min(limit, self.outstanding_count))
        encoded: list[bytes] = []
        for sequence, record in list(self._outstanding.items())[:count]:
            record.sent_at_ms = now
            record.transmissions += 1
            self._boundary_sent_ms[record.boundary] = now
            encoded.append(
                MlrPacket(
                    self.handle,
                    record.payload,
                    True,
                    sequence_number=sequence,
                    request_number=self.receive_next,
                ).encode()
            )
        if encoded:
            self._clear_ack_pending()
        return tuple(encoded)

    def retransmit_due(self, *, now_ms: float | None = None) -> tuple[bytes, ...]:
        """Apply native timeout backoff and return the packets for this retry window."""
        now = self._now(now_ms)
        deadline = self.retransmit_deadline_ms
        if deadline is None or now < deadline:
            return ()
        self.timeout_count += 1
        if self.send_window > 1:
            self.send_window = max(1, self.send_window // 2)
        self.rtt.backoff()
        return self.retransmit_outstanding(now_ms=now, limit=self.send_window)
