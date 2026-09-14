from __future__ import annotations

import pytest

from garmin_proto_lab.mlr import (
    ACK_PACKET_THRESHOLD,
    DEFERRED_ACK_MS,
    INITIAL_RTO_MS,
    INITIAL_WINDOW_PACKETS,
    MAX_BACKOFF_RTO_MS,
    MlrError,
    MlrPacket,
    MlrRttEstimator,
    ReliableMlrSession,
    fragment_blob,
    packets_required,
    sequence_number_count,
)


def test_native_validation_vectors() -> None:
    assert MlrPacket(0x05, b"\x01").encode() == bytes.fromhex("0501")
    assert MlrPacket(0x85, b"\xff", True, 2, 3).encode() == bytes.fromhex("d0c2ff")
    assert MlrPacket(0x81, bytes.fromhex("ffdea2"), True, 13, 11).encode() == bytes.fromhex("92cdffdea2")
    assert MlrPacket(0x87, bytes.fromhex("010203"), True, 63, 63).encode() == bytes.fromhex("ffff010203")
    for raw in ("0501", "d0c2ff", "92cdffdea2", "ffff010203"):
        packet = MlrPacket.parse(bytes.fromhex(raw))
        assert packet.encode() == bytes.fromhex(raw)


def test_native_sequence_modes_and_fragmentation_formula() -> None:
    assert sequence_number_count(0) == 64
    assert sequence_number_count(0xFF) == 8
    assert sequence_number_count(1) == 0xFF
    payload = bytes(range(40))
    packets = fragment_blob(0x80, payload, 20, start_sequence=62, request_number=7)
    assert len(packets) == packets_required(len(payload), 20) == 3
    assert [packet.sequence_number for packet in packets] == [62, 63, 0]
    assert b"".join(packet.payload for packet in packets) == payload
    assert all(len(packet.encode()) <= 20 for packet in packets)


def test_reliable_session_cumulative_ack_and_deferred_receive_ack() -> None:
    session = ReliableMlrSession(0x82, 20, clock_ms=lambda: 0.0)
    outbound = session.send_blob(b"configure", now_ms=0)
    assert len(outbound) == 1
    assert session.outstanding_count == 1
    assert session.send_window == INITIAL_WINDOW_PACKETS

    peer = MlrPacket(0x82, b"\x00\x00\x00", True, 0, 1).encode()
    result = session.receive(peer, now_ms=100)
    assert result.data == b"\x00\x00\x00"
    assert result.newly_acked == 1
    assert session.outstanding_count == 0
    assert result.acknowledgement is None
    assert session.ack_pending_packets == 1
    assert session.send_window == INITIAL_WINDOW_PACKETS + 1
    assert session.rtt.rto_ms >= 500

    assert session.poll_ack(now_ms=100 + DEFERRED_ACK_MS - 0.1) is None
    ack = session.poll_ack(now_ms=100 + DEFERRED_ACK_MS)
    assert ack is not None
    assert MlrPacket.parse(ack).request_number == 1
    assert session.ack_pending_packets == 0

    duplicate = session.receive(peer, now_ms=200)
    assert duplicate.data is None
    assert duplicate.duplicate_or_out_of_order is True
    assert duplicate.acknowledgement is None
    flushed = session.force_ack()
    assert flushed is not None
    assert MlrPacket.parse(flushed).request_number == 1


def test_native_ack_threshold_sends_on_fifth_data_packet() -> None:
    session = ReliableMlrSession(0x80, 20, clock_ms=lambda: 0.0)
    for sequence in range(ACK_PACKET_THRESHOLD - 1):
        result = session.receive(
            MlrPacket(0x80, bytes((sequence,)), True, sequence, 0).encode(),
            now_ms=float(sequence),
        )
        assert result.data == bytes((sequence,))
        assert result.acknowledgement is None
    final_sequence = ACK_PACKET_THRESHOLD - 1
    final = session.receive(
        MlrPacket(0x80, b"x", True, final_sequence, 0).encode(),
        now_ms=float(final_sequence),
    )
    assert final.acknowledgement is not None
    assert MlrPacket.parse(final.acknowledgement).request_number == ACK_PACKET_THRESHOLD
    assert session.ack_pending_packets == 0


def test_rtt_estimator_native_initial_update_and_backoff() -> None:
    rtt = MlrRttEstimator()
    assert rtt.rto_ms == INITIAL_RTO_MS
    # First sample: sRTT=100, RTTVAR=50, RTO=max(500, 100+max(50,200))=500.
    assert rtt.observe(100, 100) == 500
    assert rtt.srtt_ms == pytest.approx(100)
    assert rtt.rttvar_ms == pytest.approx(50)
    # A later sample exercises alpha/beta update and remains bounded by 500 ms.
    assert rtt.observe(120, 220) >= 500
    value = rtt.rto_ms
    assert rtt.backoff() == min(MAX_BACKOFF_RTO_MS, value * 2)


def test_retransmission_timeout_halves_window_and_doubles_rto() -> None:
    session = ReliableMlrSession(0x80, 12, clock_ms=lambda: 0.0)
    sent = session.send_blob(b"0123456789ABC", now_ms=0)
    assert len(sent) == 2
    assert session.retransmit_deadline_ms == INITIAL_RTO_MS
    assert session.retransmit_due(now_ms=INITIAL_RTO_MS - 1) == ()
    retransmitted = [MlrPacket.parse(raw) for raw in session.retransmit_due(now_ms=INITIAL_RTO_MS)]
    assert [packet.sequence_number for packet in retransmitted] == [0, 1]
    assert session.send_window == INITIAL_WINDOW_PACKETS // 2
    assert session.rtt.rto_ms == 2000
    assert session.timeout_count == 1

    # Repeated timeout backoff is capped at 20 seconds.
    now = INITIAL_RTO_MS
    for _ in range(8):
        deadline = session.retransmit_deadline_ms
        assert deadline is not None
        now = max(now, deadline)
        session.retransmit_due(now_ms=now)
    assert session.rtt.rto_ms == MAX_BACKOFF_RTO_MS
    assert session.send_window == 1


def test_outbound_packet_piggybacks_receive_ack() -> None:
    session = ReliableMlrSession(0x80, 20, clock_ms=lambda: 0.0)
    incoming = session.receive(MlrPacket(0x80, b"x", True, 0, 0).encode(), now_ms=0)
    assert incoming.acknowledgement is None
    assert session.ack_pending_packets == 1
    sent = session.send_blob(b"reply", now_ms=1)
    assert session.ack_pending_packets == 0
    assert MlrPacket.parse(sent[0]).request_number == 1


def test_mlr_rejects_invalid_headers_and_window_bounds() -> None:
    with pytest.raises(MlrError):
        MlrPacket.parse(b"")
    with pytest.raises(MlrError, match="second header"):
        MlrPacket.parse(b"\x80")
    with pytest.raises(MlrError, match="0x80..0x87"):
        MlrPacket(0x90, b"x", True).encode()
    with pytest.raises(MlrError, match="send window"):
        ReliableMlrSession(0x80, 20, max_outstanding=1).send_blob(bytes(range(30)), now_ms=0)



def test_out_of_order_packet_is_not_delivered_until_missing_sequence_arrives() -> None:
    session = ReliableMlrSession(0x80, 20, clock_ms=lambda: 0.0)
    ahead = session.receive(MlrPacket(0x80, b"one", True, 1, 0).encode(), now_ms=0)
    assert ahead.data is None
    assert ahead.duplicate_or_out_of_order
    first = session.receive(MlrPacket(0x80, b"zero", True, 0, 0).encode(), now_ms=1)
    assert first.data == b"zero"
    second = session.receive(MlrPacket(0x80, b"one", True, 1, 0).encode(), now_ms=2)
    assert second.data == b"one"
    assert session.receive_next == 2
