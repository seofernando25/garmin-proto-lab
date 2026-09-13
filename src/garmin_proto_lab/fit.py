"""Small independent FIT container inspector used after GFDI file transfer.

It intentionally does not implement the complete FIT profile.  It validates the
binary container enough to locate the standard File ID message (global message
0) and recover its file-type field.  That is sufficient to classify dynamic
GFDI directory entries without guessing a remote file index.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .crc import crc16_arc


class FitError(ValueError):
    pass


class FitFileType(IntEnum):
    SETTINGS = 2
    SPORT = 3
    ACTIVITY = 4
    WORKOUT = 5
    COURSE = 6
    SCHEDULES = 7
    LOCATIONS = 8
    WEIGHT = 9
    TOTALS = 10
    GOALS = 11
    MAP = 12
    DEBUG = 13
    BLOOD_PRESSURE = 14
    MONITORING_A = 15
    MONITORING_B = 32
    GOLF_SWING = 36
    GOLF_CLUB = 37
    SLEEP_DATA = 49
    USER_BEHAVIOR_LOG = 52
    INVALID = 255


FIT_DATA_TYPE = 128
ACTIVITY_HEALTH_FILE_TYPES = frozenset(
    {
        FitFileType.ACTIVITY,
        FitFileType.WEIGHT,
        FitFileType.BLOOD_PRESSURE,
        FitFileType.MONITORING_A,
        FitFileType.MONITORING_B,
        FitFileType.SLEEP_DATA,
    }
)


@dataclass(frozen=True, slots=True)
class FitHeader:
    header_size: int
    protocol_version: int
    profile_version: int
    data_size: int
    header_crc: int | None

    @property
    def data_start(self) -> int:
        return self.header_size

    @property
    def data_end(self) -> int:
        return self.header_size + self.data_size


@dataclass(frozen=True, slots=True)
class FitFieldDefinition:
    number: int
    size: int
    base_type: int


@dataclass(frozen=True, slots=True)
class FitDefinition:
    global_message_number: int
    little_endian: bool
    fields: tuple[FitFieldDefinition, ...]
    developer_field_sizes: tuple[int, ...] = ()

    @property
    def record_size(self) -> int:
        return sum(field.size for field in self.fields) + sum(self.developer_field_sizes)


@dataclass(frozen=True, slots=True)
class FitInspection:
    header: FitHeader
    file_type_raw: int | None

    @property
    def file_type(self) -> FitFileType | int | None:
        if self.file_type_raw is None:
            return None
        try:
            return FitFileType(self.file_type_raw)
        except ValueError:
            return self.file_type_raw

    @property
    def is_activity_or_health(self) -> bool:
        value = self.file_type
        return isinstance(value, FitFileType) and value in ACTIVITY_HEALTH_FILE_TYPES


def parse_fit_header(data: bytes, *, verify_header_crc: bool = True) -> FitHeader:
    if len(data) < 12:
        raise FitError("FIT file is shorter than the minimum header")
    header_size = data[0]
    if header_size < 12:
        raise FitError(f"invalid FIT header size {header_size}")
    if header_size > len(data):
        raise FitError("FIT header is truncated")
    if data[8:12] != b".FIT":
        raise FitError("FIT signature is missing")
    data_size = int.from_bytes(data[4:8], "little")
    if header_size + data_size > len(data):
        raise FitError("FIT data section is truncated")
    header_crc: int | None = None
    if header_size >= 14:
        header_crc = int.from_bytes(data[header_size - 2 : header_size], "little")
        # FIT permits a zero header CRC.  When present, validate it with the
        # same reflected 0xA001 CRC used elsewhere in the stack.
        if verify_header_crc and header_crc != 0:
            calculated = crc16_arc(data[: header_size - 2])
            if calculated != header_crc:
                raise FitError(
                    f"FIT header CRC mismatch expected=0x{header_crc:04x} calculated=0x{calculated:04x}"
                )
    return FitHeader(
        header_size=header_size,
        protocol_version=data[1],
        profile_version=int.from_bytes(data[2:4], "little"),
        data_size=data_size,
        header_crc=header_crc,
    )


def _read_definition(data: bytes, pos: int, header: int, end: int) -> tuple[int, int, FitDefinition]:
    local_message = header & 0x0F
    developer_fields = bool(header & 0x20)
    if pos + 5 > end:
        raise FitError("truncated FIT definition record")
    # one reserved byte
    architecture = data[pos + 1]
    if architecture not in (0, 1):
        raise FitError(f"invalid FIT definition architecture {architecture}")
    little = architecture == 0
    order = "little" if little else "big"
    global_message = int.from_bytes(data[pos + 2 : pos + 4], order)
    field_count = data[pos + 4]
    pos += 5
    field_bytes = field_count * 3
    if pos + field_bytes > end:
        raise FitError("truncated FIT field definitions")
    fields: list[FitFieldDefinition] = []
    for _ in range(field_count):
        fields.append(FitFieldDefinition(data[pos], data[pos + 1], data[pos + 2]))
        pos += 3
    developer_sizes: list[int] = []
    if developer_fields:
        if pos >= end:
            raise FitError("missing FIT developer-field count")
        count = data[pos]
        pos += 1
        bytes_needed = count * 3
        if pos + bytes_needed > end:
            raise FitError("truncated FIT developer-field definitions")
        for _ in range(count):
            # field number, size, developer data index
            developer_sizes.append(data[pos + 1])
            pos += 3
    return pos, local_message, FitDefinition(global_message, little, tuple(fields), tuple(developer_sizes))


def _data_local_message(header: int) -> int:
    if header & 0x80:
        # compressed timestamp header: bits 5..6 identify local message 0..3
        return (header >> 5) & 0x03
    return header & 0x0F


def inspect_fit(data: bytes) -> FitInspection:
    header = parse_fit_header(data)
    pos = header.data_start
    end = header.data_end
    definitions: dict[int, FitDefinition] = {}
    file_type_raw: int | None = None

    while pos < end:
        record_header = data[pos]
        pos += 1
        if record_header & 0x40 and not record_header & 0x80:
            pos, local, definition = _read_definition(data, pos, record_header, end)
            definitions[local] = definition
            continue

        local = _data_local_message(record_header)
        definition = definitions.get(local)
        if definition is None:
            raise FitError(f"data record references undefined local message {local}")
        record_start = pos
        record_end = pos + definition.record_size
        if record_end > end:
            raise FitError("truncated FIT data record")
        if definition.global_message_number == 0 and file_type_raw is None:
            cursor = record_start
            for field in definition.fields:
                field_end = cursor + field.size
                if field.number == 0 and field.size:
                    raw = data[cursor:field_end]
                    # File ID type is normally enum/u8. Preserve multi-byte
                    # encodings defensively using the definition architecture.
                    file_type_raw = int.from_bytes(raw, "little" if definition.little_endian else "big")
                    break
                cursor = field_end
        pos = record_end
        if file_type_raw is not None:
            break

    return FitInspection(header, file_type_raw)


def classify_directory_fit_subtype(data_type: int, subtype: int) -> FitFileType | int | None:
    if data_type != FIT_DATA_TYPE:
        return None
    try:
        return FitFileType(subtype)
    except ValueError:
        return subtype


def is_activity_or_health_subtype(data_type: int, subtype: int) -> bool:
    kind = classify_directory_fit_subtype(data_type, subtype)
    return isinstance(kind, FitFileType) and kind in ACTIVITY_HEALTH_FILE_TYPES
