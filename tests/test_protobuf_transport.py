from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.protobuf_transport import (
    CancelProtobuf,
    ProtobufChunk,
    ProtobufChunkAck,
    ProtobufChunkStatus,
    ProtobufReassembler,
    ProtobufTransportError,
    chunk_protobuf,
)


@given(
    st.integers(min_value=0, max_value=0xFFFF),
    st.integers(min_value=0, max_value=0xFFFFFFFF),
    st.integers(min_value=0, max_value=0xFFFFFFFF),
    st.binary(max_size=1024),
)
def test_chunk_header_roundtrip(request_id: int, offset: int, total: int, data: bytes) -> None:
    chunk = ProtobufChunk(request_id, offset, total, len(data), data)
    assert ProtobufChunk.parse(chunk.encode()) == chunk


def test_chunking_uses_fourteen_byte_header() -> None:
    data = bytes(range(100))
    chunks = chunk_protobuf(data, request_id=0x1234, gfdi_payload_limit=40)
    assert [len(x.data) for x in chunks] == [26, 26, 26, 22]
    assert [x.data_offset for x in chunks] == [0, 26, 52, 78]
    assert all(len(x.encode()) <= 40 for x in chunks)
    assert b"".join(x.data for x in chunks) == data


def test_ack_layout_and_status() -> None:
    ack = ProtobufChunkAck(7, 1234, False, ProtobufChunkStatus.NO_ERROR)
    raw = ack.encode()
    assert len(raw) == 8
    assert ProtobufChunkAck.parse(raw) == ack
    failed = ProtobufChunkAck(7, 1234, True, ProtobufChunkStatus.MISSING_PACKET)
    assert ProtobufChunkAck.parse(failed.encode()) == failed


def test_reassembler_normal_duplicate_missing_and_overrun() -> None:
    data = b"abcdefghijklmnopqrstuvwxyz"
    chunks = chunk_protobuf(data, request_id=3, gfdi_payload_limit=24)  # ten data bytes each
    r = ProtobufReassembler(3)

    first = r.accept(chunks[0])
    assert first.status is ProtobufChunkStatus.NO_ERROR
    assert first.completed is None
    assert first.ack.failed is False

    duplicate = r.accept(chunks[0])
    assert duplicate.status is ProtobufChunkStatus.DUPLICATE_PACKET
    assert duplicate.ack.failed is False
    assert r.offset == len(chunks[0].data)

    # Skip one packet: the receiver's buffered size is lower than the incoming offset.
    missing = r.accept(chunks[2])
    assert missing.status is ProtobufChunkStatus.MISSING_PACKET
    assert missing.ack.failed is True
    assert r.offset == len(chunks[0].data)

    assert r.accept(chunks[1]).status is ProtobufChunkStatus.NO_ERROR
    final = r.accept(chunks[2])
    assert final.status is ProtobufChunkStatus.NO_ERROR
    assert final.completed == data

    bad = ProtobufChunk(3, len(data), len(data), 1, b"x")
    over = r.accept(bad)
    # Current buffer is now greater than that packet's offset only if the
    # offset regresses. At equal completed offset, adding one exceeds total.
    assert over.status is ProtobufChunkStatus.EXCEEDED_PROTOBUF_LENGTH


def test_reassembler_unknown_request_and_changed_total() -> None:
    r = ProtobufReassembler(5, total_length=10)
    unknown = r.accept(ProtobufChunk(6, 0, 10, 1, b"x"))
    assert unknown.status is ProtobufChunkStatus.UNKNOWN_REQUEST_ID
    changed = r.accept(ProtobufChunk(5, 0, 11, 1, b"x"))
    assert changed.status is ProtobufChunkStatus.EXCEEDED_PROTOBUF_LENGTH


def test_cancel_payload() -> None:
    assert CancelProtobuf.parse(CancelProtobuf(0xBEEF).encode()).request_id == 0xBEEF
    with pytest.raises(ProtobufTransportError):
        CancelProtobuf.parse(b"\x01")


def test_parser_rejects_bad_lengths() -> None:
    with pytest.raises(ProtobufTransportError):
        ProtobufChunk.parse(b"x" * 13)
    raw = ProtobufChunk(1, 0, 3, 3, b"abc").encode()
    with pytest.raises(ProtobufTransportError):
        ProtobufChunk.parse(raw + b"x")
    with pytest.raises(ProtobufTransportError):
        ProtobufChunkAck.parse(b"\x00" * 7)
    with pytest.raises(ProtobufTransportError):
        chunk_protobuf(b"abc", 1, 14)
