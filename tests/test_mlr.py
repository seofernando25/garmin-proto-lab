from __future__ import annotations

import pytest

from garmin_proto_lab.mlr import (
    MlrError,
    MlrPacket,
    ReliableMlrSession,
    fragment_blob,
    packets_required,
)


def test_native_validation_vectors() -> None:
    assert MlrPacket(0x05, b"\x01").encode() == bytes.fromhex("0501")
    assert MlrPacket(0x85, b"\xff", True, 2, 3).encode() == bytes.fromhex("d0c2ff")
    assert MlrPacket(0x81, bytes.fromhex("ffdea2"), True, 13, 11).encode() == bytes.fromhex("92cdffdea2")
    assert MlrPacket(0x87, bytes.fromhex("010203"), True, 63, 63).encode() == bytes.fromhex("ffff010203")
    for raw in ("0501", "d0c2ff", "92cdffdea2", "ffff010203"):
        packet = MlrPacket.parse(bytes.fromhex(raw))
        assert packet.encode() == bytes.fromhex(raw)


def test_fragmentation_matches_native_packet_count_formula() -> None:
    payload = bytes(range(40))
    packets = fragment_blob(0x80, payload, 20, start_sequence=62, request_number=7)
    assert len(packets) == packets_required(len(payload), 20) == 3
    assert [packet.sequence_number for packet in packets] == [62, 63, 0]
    assert b"".join(packet.payload for packet in packets) == payload
    assert all(len(packet.encode()) <= 20 for packet in packets)


def test_reliable_session_config_send_receive_ack_and_duplicate() -> None:
    session = ReliableMlrSession(0x82, 20)
    outbound = session.send_blob(b"configure")
    assert len(outbound) == 1
    assert session.outstanding_count == 1
    peer = MlrPacket(0x82, b"\x00\x00\x00", True, 0, 1).encode()
    result = session.receive(peer)
    assert result.data == b"\x00\x00\x00"
    assert result.newly_acked == 1
    assert session.outstanding_count == 0
    assert MlrPacket.parse(result.acknowledgement or b"").request_number == 1
    duplicate = session.receive(peer)
    assert duplicate.data is None
    assert duplicate.duplicate_or_out_of_order is True
    assert MlrPacket.parse(duplicate.acknowledgement or b"").request_number == 1


def test_reliable_session_retransmit_preserves_sequence_and_updates_ack() -> None:
    session = ReliableMlrSession(0x80, 12)
    sent = session.send_blob(b"0123456789ABC")
    assert len(sent) == 2
    received = session.receive(MlrPacket(0x80, b"x", True, 0, 0).encode())
    assert received.data == b"x"
    retransmitted = [MlrPacket.parse(raw) for raw in session.retransmit_outstanding()]
    assert [packet.sequence_number for packet in retransmitted] == [0, 1]
    assert all(packet.request_number == 1 for packet in retransmitted)


def test_mlr_rejects_invalid_headers_and_bounds() -> None:
    with pytest.raises(MlrError):
        MlrPacket.parse(b"")
    with pytest.raises(MlrError, match="second header"):
        MlrPacket.parse(b"\x80")
    with pytest.raises(MlrError, match="0x80..0x87"):
        MlrPacket(0x90, b"x", True).encode()
    with pytest.raises(MlrError, match="outstanding"):
        ReliableMlrSession(0x80, 20, max_outstanding=1).send_blob(bytes(range(30)))
