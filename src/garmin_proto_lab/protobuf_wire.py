"""Minimal bounded Protocol Buffers wire codec used by GFDI Smart messages.

Only wire-format primitives are implemented.  This avoids importing Garmin's
generated protobuf classes while preserving unknown fields for interoperability
analysis.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ProtobufWireError(ValueError):
    pass


class WireType(IntEnum):
    VARINT = 0
    FIXED64 = 1
    LENGTH_DELIMITED = 2
    FIXED32 = 5


@dataclass(frozen=True, slots=True)
class WireField:
    number: int
    wire_type: WireType
    value: int | bytes


def encode_varint(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ProtobufWireError("varint value must fit uint64")
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def decode_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    pos = offset
    for _ in range(10):
        if pos >= len(data):
            raise ProtobufWireError("truncated varint")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            if value > 0xFFFFFFFFFFFFFFFF:
                raise ProtobufWireError("varint exceeds uint64")
            return value, pos
        shift += 7
    raise ProtobufWireError("varint exceeds ten bytes")


def _key(number: int, wire_type: WireType) -> bytes:
    if not 1 <= number <= 0x1FFFFFFF:
        raise ProtobufWireError("field number out of protobuf range")
    return encode_varint((number << 3) | int(wire_type))


def encode_uint(number: int, value: int) -> bytes:
    return _key(number, WireType.VARINT) + encode_varint(value)


def encode_bool(number: int, value: bool) -> bytes:
    return encode_uint(number, int(value))


def encode_bytes(number: int, value: bytes) -> bytes:
    raw = bytes(value)
    return _key(number, WireType.LENGTH_DELIMITED) + encode_varint(len(raw)) + raw


def encode_string(number: int, value: str) -> bytes:
    return encode_bytes(number, value.encode("utf-8"))


def encode_message(number: int, serialized_message: bytes) -> bytes:
    return encode_bytes(number, serialized_message)


def parse_fields(
    data: bytes,
    *,
    max_fields: int = 4096,
    max_length_delimited: int = 16 * 1024 * 1024,
) -> tuple[WireField, ...]:
    """Parse one protobuf message while preserving field order and unknowns."""
    pos = 0
    fields: list[WireField] = []
    while pos < len(data):
        if len(fields) >= max_fields:
            raise ProtobufWireError("protobuf field count exceeds configured bound")
        raw_key, pos = decode_varint(data, pos)
        number = raw_key >> 3
        raw_wire = raw_key & 7
        if number == 0:
            raise ProtobufWireError("protobuf field number zero is invalid")
        try:
            wire = WireType(raw_wire)
        except ValueError as exc:
            raise ProtobufWireError(f"unsupported protobuf wire type {raw_wire}") from exc

        if wire is WireType.VARINT:
            value, pos = decode_varint(data, pos)
        elif wire is WireType.FIXED64:
            end = pos + 8
            if end > len(data):
                raise ProtobufWireError("truncated fixed64 field")
            value = bytes(data[pos:end])
            pos = end
        elif wire is WireType.LENGTH_DELIMITED:
            size, pos = decode_varint(data, pos)
            if size > max_length_delimited:
                raise ProtobufWireError("length-delimited field exceeds configured bound")
            end = pos + size
            if end > len(data):
                raise ProtobufWireError("truncated length-delimited field")
            value = bytes(data[pos:end])
            pos = end
        else:  # FIXED32
            end = pos + 4
            if end > len(data):
                raise ProtobufWireError("truncated fixed32 field")
            value = bytes(data[pos:end])
            pos = end
        fields.append(WireField(number, wire, value))
    return tuple(fields)


def values(fields: tuple[WireField, ...], number: int) -> tuple[int | bytes, ...]:
    return tuple(field.value for field in fields if field.number == number)


def last_varint(fields: tuple[WireField, ...], number: int) -> int | None:
    result: int | None = None
    for field in fields:
        if field.number == number:
            if field.wire_type is not WireType.VARINT or not isinstance(field.value, int):
                raise ProtobufWireError(f"field {number} is not a varint")
            result = field.value
    return result


def last_bytes(fields: tuple[WireField, ...], number: int) -> bytes | None:
    result: bytes | None = None
    for field in fields:
        if field.number == number:
            if field.wire_type is not WireType.LENGTH_DELIMITED or not isinstance(field.value, bytes):
                raise ProtobufWireError(f"field {number} is not length-delimited")
            result = field.value
    return result
