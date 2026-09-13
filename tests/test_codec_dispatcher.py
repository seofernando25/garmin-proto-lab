from __future__ import annotations

import pytest

from garmin_proto_lab.codec import DecodeProblem, DecodedPacket, GfdiWireCodec, SecureOutbound
from garmin_proto_lab.cobs import frame as cobs_frame
from garmin_proto_lab.dispatcher import (
    HandledMessage,
    MatchedResponse,
    MessageDispatcher,
    RequestTracker,
    UnmatchedResponse,
    UnknownMessage,
)
from garmin_proto_lab.frame import Frame, ResponseStatus, encode_ack, encode_frame
from garmin_proto_lab.secure_session import SecurePacketDecoder

KEY = bytes.fromhex("00112233445566778899aabbccddeeff")
IV = bytes.fromhex("a1b2c3d4")


def _only(result):
    assert len(result) == 1
    return result[0]


def test_wire_codec_partial_reassembly_duplicate_and_reordering() -> None:
    codec = GfdiWireCodec()
    first = Frame(5023, b"battery")
    second = Frame(5052, b"time")
    wire1 = codec.encode(first)
    wire2 = codec.encode(second)

    # A packet split at arbitrary transport boundaries is not emitted early.
    split = len(wire1) // 2
    assert codec.feed(wire1[:split]) == []
    got = _only(codec.feed(wire1[split:]))
    assert isinstance(got, DecodedPacket)
    assert got.frame == first

    # Duplicates are preserved as observations rather than silently dropped.
    dup = codec.feed(wire1 + wire1)
    assert [x.frame for x in dup if isinstance(x, DecodedPacket)] == [first, first]

    # The wire codec preserves input order; it does not invent sequencing.
    reordered = codec.feed(wire2 + wire1)
    assert [x.frame for x in reordered if isinstance(x, DecodedPacket)] == [second, first]


def test_wire_codec_reports_frame_length_and_crc_errors() -> None:
    good = bytearray(encode_frame(Frame(5024, b"abc")))
    bad_len = bytearray(good)
    bad_len[0] ^= 1
    problem = _only(GfdiWireCodec().feed(cobs_frame(bytes(bad_len))))
    assert isinstance(problem, DecodeProblem)
    assert problem.stage == "frame"
    assert "length mismatch" in problem.reason

    bad_crc = bytearray(good)
    bad_crc[4] ^= 0x20
    problem = _only(GfdiWireCodec().feed(cobs_frame(bytes(bad_crc))))
    assert isinstance(problem, DecodeProblem)
    assert problem.stage == "frame"
    assert "CRC mismatch" in problem.reason


def test_wire_codec_reports_malformed_and_oversized_cobs() -> None:
    codec = GfdiWireCodec(max_encoded_packet=8)
    # COBS code 5 cannot be satisfied by the single following byte.
    problem = _only(codec.feed(bytes.fromhex("00051100")))
    assert isinstance(problem, DecodeProblem)
    assert problem.stage == "cobs"

    problem = _only(codec.feed(b"\x00" + b"\x01" * 9))
    assert isinstance(problem, DecodeProblem)
    assert problem.stage == "cobs"
    assert "exceeds" in problem.reason


def test_secure_codec_roundtrip_and_reordered_counter_rejection() -> None:
    outbound = SecureOutbound(KEY, IV)
    inbound = SecurePacketDecoder(KEY, IV)
    sender = GfdiWireCodec(outbound_secure=outbound)
    receiver = GfdiWireCodec(inbound_secure=inbound)

    wire0 = sender.encode(Frame(5023, b"one"))  # counter 0
    wire1 = sender.encode(Frame(5023, b"two"))  # counter 1
    # Accept newer packet first. Counter 0 then becomes a backwards packet.
    got1 = _only(receiver.feed(wire1))
    assert isinstance(got1, DecodedPacket)
    assert got1.frame.payload == b"two"
    got0 = _only(receiver.feed(wire0))
    assert isinstance(got0, DecodeProblem)
    assert got0.stage == "secure"
    assert "backwards" in got0.reason


def test_dispatcher_preserves_unknown_and_known_unhandled_messages() -> None:
    dispatcher = MessageDispatcher()
    unknown = dispatcher.dispatch(Frame(29999, b"mystery"))
    assert isinstance(unknown, UnknownMessage)
    assert unknown.known_name is None
    assert unknown.frame.payload == b"mystery"

    known_unhandled = dispatcher.dispatch(Frame(5023, b"raw"))
    assert isinstance(known_unhandled, UnknownMessage)
    assert known_unhandled.known_name == "battery_status"

    dispatcher.register(5023, lambda frame: len(frame.payload))
    handled = dispatcher.dispatch(Frame(5023, b"raw"))
    assert isinstance(handled, HandledMessage)
    assert handled.result == 3


def test_request_tracker_matches_type_and_transaction_without_silent_loss() -> None:
    tracker = RequestTracker(timeout_seconds=45)
    pending = tracker.allocate(5024, now=100.0)
    assert pending.transaction_id == 0

    wrong = Frame(5000, 5052 .to_bytes(2, "little") + bytes([0]), transaction_id=0)
    result = tracker.resolve(wrong)
    assert isinstance(result, UnmatchedResponse)
    assert len(tracker.pending) == 1

    good_raw = encode_ack(5024, ResponseStatus.ACK, b"ok", transaction_id=0)
    from garmin_proto_lab.frame import decode_frame
    result = tracker.resolve(decode_frame(good_raw))
    assert isinstance(result, MatchedResponse)
    assert result.acknowledgement.payload == b"ok"
    assert tracker.pending == ()


def test_request_tracker_timeout_reset_and_id_exhaustion() -> None:
    tracker = RequestTracker(timeout_seconds=5)
    first = tracker.allocate(5002, now=10)
    second = tracker.allocate(5003, now=11)
    assert tracker.expire(14.9) == []
    expired = tracker.expire(15.0)
    assert [x.request for x in expired] == [first]
    assert tracker.pending == (second,)
    assert tracker.reset() == (second,)
    assert tracker.pending == ()

    for i in range(32):
        tracker.allocate(5000 + i, now=0)
    with pytest.raises(RuntimeError, match="all 32"):
        tracker.allocate(5024, now=0)
