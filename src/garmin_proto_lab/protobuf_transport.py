"""GFDI Protobuf Request/Response transport (5043/5044/5045).

This module implements only the byte transport around serialized protobuf
messages. It does not depend on Garmin's generated protobuf classes.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ProtobufTransportError(ValueError):
    pass


class ProtobufChunkStatus(IntEnum):
    NO_ERROR = 0
    UNKNOWN_REQUEST_ID = 100
    DUPLICATE_PACKET = 101
    MISSING_PACKET = 102
    EXCEEDED_PROTOBUF_LENGTH = 103
    PARSE_ERROR = 200
    UNKNOWN_PROTOBUF_MESSAGE = 201


@dataclass(frozen=True, slots=True)
class ProtobufChunk:
    request_id: int
    data_offset: int
    total_length: int
    chunk_length: int
    data: bytes

    def encode(self) -> bytes:
        if not 0 <= self.request_id <= 0xFFFF:
            raise ProtobufTransportError("request_id out of uint16 range")
        for value, label in (
            (self.data_offset, "data_offset"),
            (self.total_length, "total_length"),
            (self.chunk_length, "chunk_length"),
        ):
            if not 0 <= value <= 0xFFFFFFFF:
                raise ProtobufTransportError(f"{label} out of uint32 range")
        if self.chunk_length != len(self.data):
            raise ProtobufTransportError("chunk_length does not match data bytes")
        return (
            self.request_id.to_bytes(2, "little")
            + self.data_offset.to_bytes(4, "little")
            + self.total_length.to_bytes(4, "little")
            + self.chunk_length.to_bytes(4, "little")
            + self.data
        )

    @classmethod
    def parse(cls, payload: bytes) -> "ProtobufChunk":
        if len(payload) < 14:
            raise ProtobufTransportError("protobuf transport payload shorter than 14 bytes")
        request_id = int.from_bytes(payload[0:2], "little")
        data_offset = int.from_bytes(payload[2:6], "little")
        total_length = int.from_bytes(payload[6:10], "little")
        chunk_length = int.from_bytes(payload[10:14], "little")
        end = 14 + chunk_length
        if end > len(payload):
            raise ProtobufTransportError("protobuf chunk data is truncated")
        if end != len(payload):
            raise ProtobufTransportError("trailing bytes after protobuf chunk data")
        return cls(request_id, data_offset, total_length, chunk_length, bytes(payload[14:end]))


@dataclass(frozen=True, slots=True)
class ProtobufChunkAck:
    request_id: int
    data_offset: int
    failed: bool
    status: ProtobufChunkStatus | int

    def encode(self) -> bytes:
        if not 0 <= self.request_id <= 0xFFFF:
            raise ProtobufTransportError("request_id out of uint16 range")
        if not 0 <= self.data_offset <= 0xFFFFFFFF:
            raise ProtobufTransportError("data_offset out of uint32 range")
        status_value = int(self.status)
        if not 0 <= status_value <= 0xFF:
            raise ProtobufTransportError("status does not fit one transport byte")
        return (
            self.request_id.to_bytes(2, "little")
            + self.data_offset.to_bytes(4, "little")
            + bytes([int(self.failed), status_value])
        )

    @classmethod
    def parse(cls, payload: bytes) -> "ProtobufChunkAck":
        if len(payload) < 8:
            raise ProtobufTransportError("protobuf chunk ACK shorter than eight bytes")
        if len(payload) != 8:
            raise ProtobufTransportError("protobuf chunk ACK must be exactly eight bytes")
        raw_status = payload[7]
        try:
            status: ProtobufChunkStatus | int = ProtobufChunkStatus(raw_status)
        except ValueError:
            status = raw_status
        return cls(
            request_id=int.from_bytes(payload[0:2], "little"),
            data_offset=int.from_bytes(payload[2:6], "little"),
            failed=payload[6] != 0,
            status=status,
        )


@dataclass(frozen=True, slots=True)
class CancelProtobuf:
    request_id: int

    def encode(self) -> bytes:
        if not 0 <= self.request_id <= 0xFFFF:
            raise ProtobufTransportError("request_id out of uint16 range")
        return self.request_id.to_bytes(2, "little")

    @classmethod
    def parse(cls, payload: bytes) -> "CancelProtobuf":
        if len(payload) < 2:
            raise ProtobufTransportError("5045 cancel payload shorter than two bytes")
        return cls(int.from_bytes(payload[:2], "little"))


def chunk_protobuf(serialized: bytes, request_id: int, gfdi_payload_limit: int) -> list[ProtobufChunk]:
    """Split serialized protobuf bytes into the statically observed 14-byte header packets."""
    max_data = gfdi_payload_limit - 14
    if max_data <= 0:
        raise ProtobufTransportError("GFDI payload limit is too small for protobuf transport")
    if len(serialized) > 0xFFFFFFFF:
        raise ProtobufTransportError("protobuf exceeds uint32 transport length")
    if not serialized:
        # The Garmin sender loops while offset < length, so an empty protobuf
        # emits no transport packet. Preserve that behavior explicitly.
        return []
    out: list[ProtobufChunk] = []
    offset = 0
    while offset < len(serialized):
        data = serialized[offset : offset + max_data]
        out.append(ProtobufChunk(request_id, offset, len(serialized), len(data), data))
        offset += len(data)
    return out


@dataclass(frozen=True, slots=True)
class ReassemblyResult:
    status: ProtobufChunkStatus
    ack: ProtobufChunkAck
    completed: bytes | None = None


class ProtobufReassembler:
    """Offset/length reassembly for one request ID.

    Parsing the final protobuf semantic message is intentionally left to the
    caller.  `mark_parse_error` / `mark_unknown_message` can be used to form
    the same error ACK class after semantic parsing.
    """

    def __init__(self, request_id: int, total_length: int | None = None) -> None:
        if not 0 <= request_id <= 0xFFFF:
            raise ProtobufTransportError("request_id out of uint16 range")
        self.request_id = request_id
        self.total_length = total_length
        self.buffer = bytearray()

    @property
    def offset(self) -> int:
        return len(self.buffer)

    def _ack(self, offset: int, status: ProtobufChunkStatus) -> ProtobufChunkAck:
        # Static receiver marks NO_ERROR and DUPLICATE_PACKET as transport
        # success (`failed=0`), all other statuses as failed=1.
        failed = status not in (ProtobufChunkStatus.NO_ERROR, ProtobufChunkStatus.DUPLICATE_PACKET)
        return ProtobufChunkAck(self.request_id, offset, failed, status)

    def accept(self, chunk: ProtobufChunk) -> ReassemblyResult:
        if chunk.request_id != self.request_id:
            status = ProtobufChunkStatus.UNKNOWN_REQUEST_ID
            return ReassemblyResult(status, self._ack(chunk.data_offset, status))
        if self.total_length is None:
            self.total_length = chunk.total_length
        elif chunk.total_length != self.total_length:
            status = ProtobufChunkStatus.EXCEEDED_PROTOBUF_LENGTH
            return ReassemblyResult(status, self._ack(chunk.data_offset, status))

        current = len(self.buffer)
        if current < chunk.data_offset:
            status = ProtobufChunkStatus.MISSING_PACKET
            return ReassemblyResult(status, self._ack(chunk.data_offset, status))
        if current > chunk.data_offset:
            status = ProtobufChunkStatus.DUPLICATE_PACKET
            return ReassemblyResult(status, self._ack(chunk.data_offset, status))
        if current + len(chunk.data) > self.total_length:
            status = ProtobufChunkStatus.EXCEEDED_PROTOBUF_LENGTH
            return ReassemblyResult(status, self._ack(chunk.data_offset, status))

        self.buffer.extend(chunk.data)
        complete = bytes(self.buffer) if len(self.buffer) == self.total_length else None
        status = ProtobufChunkStatus.NO_ERROR
        return ReassemblyResult(status, self._ack(chunk.data_offset, status), complete)

    def semantic_failure(self, offset: int, status: ProtobufChunkStatus) -> ReassemblyResult:
        if status not in (ProtobufChunkStatus.PARSE_ERROR, ProtobufChunkStatus.UNKNOWN_PROTOBUF_MESSAGE):
            raise ValueError("semantic failure must be PARSE_ERROR or UNKNOWN_PROTOBUF_MESSAGE")
        return ReassemblyResult(status, self._ack(offset, status))
