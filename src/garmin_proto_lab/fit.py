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
    file_crc: int

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


def inspect_fit(data: bytes, *, verify_file_crc: bool = True) -> FitInspection:
    header = parse_fit_header(data)
    crc_offset = header.data_end
    if len(data) < crc_offset + 2:
        raise FitError("FIT file CRC is truncated")
    file_crc = int.from_bytes(data[crc_offset : crc_offset + 2], "little")
    if verify_file_crc:
        calculated = crc16_arc(data[:crc_offset])
        if calculated != file_crc:
            raise FitError(
                f"FIT file CRC mismatch expected=0x{file_crc:04x} calculated=0x{calculated:04x}"
            )
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
        compressed = bool(record_header & 0x80)
        payload_size = sum(
            field.size
            for field in definition.fields
            if not (compressed and field.number == 253)
        ) + sum(definition.developer_field_sizes)
        record_start = pos
        record_end = pos + payload_size
        if record_end > end:
            raise FitError("truncated FIT data record")
        if definition.global_message_number == 0 and file_type_raw is None:
            cursor = record_start
            for field in definition.fields:
                if compressed and field.number == 253:
                    continue
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

    return FitInspection(header, file_type_raw, file_crc)


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


def fit_file_type_from_data_type_name(name: str | None) -> FitFileType | int | None:
    """Map next-gen FileAccess names such as ``FIT_TYPE_4`` to FIT types."""
    if name is None or not name.startswith("FIT_TYPE_"):
        return None
    try:
        value = int(name.removeprefix("FIT_TYPE_"), 10)
    except ValueError:
        return None
    if not 0 <= value <= 0xFF:
        return None
    try:
        return FitFileType(value)
    except ValueError:
        return value


def is_activity_or_health_data_type_name(name: str | None) -> bool:
    kind = fit_file_type_from_data_type_name(name)
    return isinstance(kind, FitFileType) and kind in ACTIVITY_HEALTH_FILE_TYPES

# Generic FIT record decoding used after a watch file has passed transport and
# file-level integrity checks. Field numbers are kept numeric so profile
# extensions remain available even when this project has no semantic name for
# them yet.

from datetime import datetime, timezone
import struct
from typing import Any

from .time_sync import GARMIN_EPOCH_UNIX_SECONDS


@dataclass(frozen=True, slots=True)
class FitFieldValue:
    number: int
    base_type: int
    raw: bytes
    value: Any


FIT_GLOBAL_MESSAGE_NAMES: dict[int, str] = {
    0: "FILE_ID",
    18: "SESSION",
    19: "LAP",
    20: "RECORD",
    21: "EVENT",
    23: "DEVICE_INFO",
    30: "WEIGHT_SCALE",
    34: "ACTIVITY",
    51: "BLOOD_PRESSURE",
    55: "MONITORING",
    78: "HRV",
    103: "MONITORING_INFO",
    132: "HR",
    211: "MONITORING_HR_DATA",
    227: "STRESS_LEVEL",
    269: "SPO2_DATA",
    275: "SLEEP_LEVEL",
    279: "MONITORING_ENVIRONMENT",
    297: "RESPIRATION_RATE",
    314: "HSA_BODY_BATTERY_DATA",
}


@dataclass(frozen=True, slots=True)
class FitDataRecord:
    global_message_number: int
    local_message_number: int
    fields: tuple[FitFieldValue, ...]
    timestamp: int | None = None
    developer_data: bytes = b""

    @property
    def message_name(self) -> str | None:
        return FIT_GLOBAL_MESSAGE_NAMES.get(self.global_message_number)

    def field(self, number: int) -> FitFieldValue | None:
        for field in self.fields:
            if field.number == number:
                return field
        return None

    def value(self, number: int, default: Any = None) -> Any:
        field = self.field(number)
        return default if field is None else field.value


_BASE_TYPE_INFO: dict[int, tuple[str, int] | None] = {
    0: ("B", 1),   # enum
    1: ("b", 1),   # sint8
    2: ("B", 1),   # uint8
    3: ("h", 2),   # sint16
    4: ("H", 2),   # uint16
    5: ("i", 4),   # sint32
    6: ("I", 4),   # uint32
    7: None,        # string
    8: ("f", 4),   # float32
    9: ("d", 8),   # float64
    10: ("B", 1),  # uint8z
    11: ("H", 2),  # uint16z
    12: ("I", 4),  # uint32z
    13: None,       # byte
    14: ("q", 8),  # sint64
    15: ("Q", 8),  # uint64
    16: ("Q", 8),  # uint64z
}


_BASE_TYPE_INVALID: dict[int, int] = {
    0: 0xFF,
    1: 0x7F,
    2: 0xFF,
    3: 0x7FFF,
    4: 0xFFFF,
    5: 0x7FFFFFFF,
    6: 0xFFFFFFFF,
    10: 0,
    11: 0,
    12: 0,
    14: 0x7FFFFFFFFFFFFFFF,
    15: 0xFFFFFFFFFFFFFFFF,
    16: 0,
}


def _decode_fit_field(raw: bytes, base_type: int, little_endian: bool) -> Any:
    type_id = base_type & 0x1F
    if type_id == 7:
        return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
    if type_id == 13:
        return bytes(raw)
    info = _BASE_TYPE_INFO.get(type_id)
    if info is None:
        return bytes(raw)
    fmt, width = info
    if len(raw) % width:
        raise FitError(
            f"FIT field size {len(raw)} is not a multiple of base-type width {width}"
        )
    prefix = "<" if little_endian else ">"
    count = len(raw) // width
    values = struct.unpack(prefix + fmt * count, raw)
    if type_id in (8, 9):
        # FIT float invalid is represented by all-one raw bytes. Check the raw
        # element before interpreting it as an IEEE NaN.
        decoded = tuple(
            None
            if raw[index * width : (index + 1) * width] == b"\xff" * width
            else value
            for index, value in enumerate(values)
        )
    else:
        invalid = _BASE_TYPE_INVALID.get(type_id)
        decoded = tuple(None if invalid is not None and value == invalid else value for value in values)
    return decoded[0] if count == 1 else decoded


def _verify_fit_file_crc(data: bytes, header: FitHeader) -> int:
    crc_offset = header.data_end
    if len(data) < crc_offset + 2:
        raise FitError("FIT file CRC is truncated")
    file_crc = int.from_bytes(data[crc_offset : crc_offset + 2], "little")
    calculated = crc16_arc(data[:crc_offset])
    if calculated != file_crc:
        raise FitError(
            f"FIT file CRC mismatch expected=0x{file_crc:04x} calculated=0x{calculated:04x}"
        )
    return file_crc


def parse_fit_records(data: bytes, *, verify_file_crc: bool = True) -> tuple[FitDataRecord, ...]:
    """Decode all FIT data records while preserving numeric profile fields.

    Definition records are applied per local-message slot. Compressed timestamp
    headers reconstruct field 253 from the previous timestamp and consume no
    timestamp bytes from the data payload, as defined by the FIT container.
    """
    header = parse_fit_header(data)
    if verify_file_crc:
        _verify_fit_file_crc(data, header)
    elif len(data) < header.data_end + 2:
        raise FitError("FIT file CRC is truncated")

    pos = header.data_start
    end = header.data_end
    definitions: dict[int, FitDefinition] = {}
    records: list[FitDataRecord] = []
    last_timestamp: int | None = None

    while pos < end:
        record_header = data[pos]
        pos += 1
        if record_header & 0x40 and not record_header & 0x80:
            pos, local, definition = _read_definition(data, pos, record_header, end)
            definitions[local] = definition
            continue

        compressed = bool(record_header & 0x80)
        local = _data_local_message(record_header)
        definition = definitions.get(local)
        if definition is None:
            raise FitError(f"data record references undefined local message {local}")

        compressed_timestamp: int | None = None
        if compressed:
            if last_timestamp is None:
                raise FitError("compressed FIT timestamp has no previous timestamp")
            offset = record_header & 0x1F
            compressed_timestamp = (last_timestamp & ~0x1F) | offset
            if compressed_timestamp < last_timestamp:
                compressed_timestamp += 0x20

        values: list[FitFieldValue] = []
        timestamp = compressed_timestamp
        for field in definition.fields:
            if compressed and field.number == 253:
                assert compressed_timestamp is not None
                values.append(
                    FitFieldValue(
                        field.number,
                        field.base_type,
                        b"",
                        compressed_timestamp,
                    )
                )
                continue
            field_end = pos + field.size
            if field_end > end:
                raise FitError("truncated FIT data field")
            raw = bytes(data[pos:field_end])
            pos = field_end
            value = _decode_fit_field(raw, field.base_type, definition.little_endian)
            values.append(FitFieldValue(field.number, field.base_type, raw, value))
            if field.number == 253 and isinstance(value, int):
                timestamp = value

        developer_length = sum(definition.developer_field_sizes)
        developer_end = pos + developer_length
        if developer_end > end:
            raise FitError("truncated FIT developer data")
        developer_data = bytes(data[pos:developer_end])
        pos = developer_end

        if timestamp is not None:
            last_timestamp = timestamp
        records.append(
            FitDataRecord(
                definition.global_message_number,
                local,
                tuple(values),
                timestamp,
                developer_data,
            )
        )

    return tuple(records)


FIT_RECORD_GLOBAL_MESSAGE = 20
FIT_SESSION_GLOBAL_MESSAGE = 18
FIT_LAP_GLOBAL_MESSAGE = 19
FIT_ACTIVITY_GLOBAL_MESSAGE = 34
FIT_SEMICIRCLE_TO_DEGREES = 180.0 / (1 << 31)


@dataclass(frozen=True, slots=True)
class ActivitySample:
    timestamp_garmin: int | None
    timestamp_utc: datetime | None
    latitude_deg: float | None
    longitude_deg: float | None
    altitude_m: float | None
    heart_rate_bpm: int | None
    cadence_rpm: int | None
    distance_m: float | None
    speed_mps: float | None
    power_w: int | None
    temperature_c: int | None


def _number(record: FitDataRecord, field_number: int) -> int | float | None:
    value = record.value(field_number)
    return value if isinstance(value, (int, float)) else None


def _scaled(value: int | float | None, scale: float, offset: float = 0.0) -> float | None:
    return None if value is None else float(value) / scale - offset


def fit_timestamp_utc(value: int | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromtimestamp(GARMIN_EPOCH_UNIX_SECONDS + value, tz=timezone.utc)


def extract_activity_samples(data: bytes) -> tuple[ActivitySample, ...]:
    """Extract standard FIT Record (global message 20) activity samples."""
    samples: list[ActivitySample] = []
    for record in parse_fit_records(data):
        if record.global_message_number != FIT_RECORD_GLOBAL_MESSAGE:
            continue
        lat = _number(record, 0)
        lon = _number(record, 1)
        altitude = _number(record, 2)
        heart = _number(record, 3)
        cadence = _number(record, 4)
        distance = _number(record, 5)
        speed = _number(record, 73)
        if speed is None:
            speed = _number(record, 6)
        power = _number(record, 7)
        temperature = _number(record, 13)
        samples.append(
            ActivitySample(
                record.timestamp,
                fit_timestamp_utc(record.timestamp),
                None if lat is None else float(lat) * FIT_SEMICIRCLE_TO_DEGREES,
                None if lon is None else float(lon) * FIT_SEMICIRCLE_TO_DEGREES,
                _scaled(altitude, 5.0, 500.0),
                int(heart) if heart is not None else None,
                int(cadence) if cadence is not None else None,
                _scaled(distance, 100.0),
                _scaled(speed, 1000.0),
                int(power) if power is not None else None,
                int(temperature) if temperature is not None else None,
            )
        )
    return tuple(samples)


def activity_sample_dict(sample: ActivitySample) -> dict[str, Any]:
    """Return a JSON-ready representation of one standard activity sample."""
    return {
        "timestamp_garmin": sample.timestamp_garmin,
        "timestamp_utc": sample.timestamp_utc.isoformat() if sample.timestamp_utc is not None else None,
        "latitude_deg": sample.latitude_deg,
        "longitude_deg": sample.longitude_deg,
        "altitude_m": sample.altitude_m,
        "heart_rate_bpm": sample.heart_rate_bpm,
        "cadence_rpm": sample.cadence_rpm,
        "distance_m": sample.distance_m,
        "speed_mps": sample.speed_mps,
        "power_w": sample.power_w,
        "temperature_c": sample.temperature_c,
    }




# Standard FIT wellness messages used by monitoring/sleep files. Field numbers,
# scales, and enum values are protocol profile facts cross-checked against the
# official Garmin FIT Profile 21.214.
FIT_WEIGHT_SCALE_GLOBAL_MESSAGE = 30
FIT_BLOOD_PRESSURE_GLOBAL_MESSAGE = 51
FIT_MONITORING_GLOBAL_MESSAGE = 55
FIT_HRV_GLOBAL_MESSAGE = 78
FIT_MONITORING_INFO_GLOBAL_MESSAGE = 103
FIT_HR_GLOBAL_MESSAGE = 132
FIT_MONITORING_HR_GLOBAL_MESSAGE = 211
FIT_STRESS_LEVEL_GLOBAL_MESSAGE = 227
FIT_SPO2_GLOBAL_MESSAGE = 269
FIT_SLEEP_LEVEL_GLOBAL_MESSAGE = 275
FIT_RESPIRATION_RATE_GLOBAL_MESSAGE = 297
FIT_BODY_BATTERY_GLOBAL_MESSAGE = 314

_SLEEP_LEVEL_NAMES = {
    0: "unmeasurable",
    1: "awake",
    2: "light",
    3: "deep",
    4: "rem",
}

_SPO2_MODE_NAMES = {
    0: "off_wrist",
    1: "spot_check",
    2: "continuous_check",
    3: "periodic",
}

_HR_TYPE_NAMES = {0: "normal", 1: "irregular"}

_BP_STATUS_NAMES = {
    0: "no_error",
    1: "error_incomplete_data",
    2: "error_no_measurement",
    3: "error_data_out_of_range",
    4: "error_irregular_heart_rate",
}

_ACTIVITY_TYPE_NAMES = {
    0: "generic",
    1: "running",
    2: "cycling",
    3: "transition",
    4: "fitness_equipment",
    5: "swimming",
    6: "walking",
    8: "sedentary",
    13: "wheelchair_pushing",
    254: "all",
}

_ACTIVITY_SUBTYPE_NAMES = {
    0: "generic",
    1: "treadmill",
    2: "street",
    3: "trail",
    4: "track",
    5: "spin",
    6: "indoor_cycling",
    7: "road",
    8: "mountain",
    9: "downhill",
    10: "recumbent",
    11: "cyclocross",
    12: "hand_cycling",
    13: "track_cycling",
    14: "indoor_rowing",
    15: "elliptical",
    16: "stair_climbing",
    17: "lap_swimming",
    18: "open_water",
    254: "all",
}



_FIT_SPORT_NAMES = {
    0: "generic", 1: "running", 2: "cycling", 3: "transition",
    4: "fitness_equipment", 5: "swimming", 6: "basketball", 7: "soccer",
    8: "tennis", 9: "american_football", 10: "training", 11: "walking",
    12: "cross_country_skiing", 13: "alpine_skiing", 14: "snowboarding",
    15: "rowing", 16: "mountaineering", 17: "hiking", 18: "multisport",
    19: "paddling", 20: "flying", 21: "e_biking", 22: "motorcycling",
    23: "boating", 24: "driving", 25: "golf", 26: "hang_gliding",
    27: "horseback_riding", 28: "hunting", 29: "fishing", 30: "inline_skating",
    31: "rock_climbing", 32: "sailing", 33: "ice_skating", 34: "sky_diving",
    35: "snowshoeing", 36: "snowmobiling", 37: "stand_up_paddleboarding",
    38: "surfing", 39: "wakeboarding", 40: "water_skiing", 41: "kayaking",
    42: "rafting", 43: "windsurfing", 44: "kitesurfing", 45: "tactical",
    46: "jumpmaster", 47: "boxing", 48: "floor_climbing", 49: "baseball",
    53: "diving", 56: "shooting", 58: "winter_sport", 59: "grinding",
    62: "hiit", 63: "video_gaming", 64: "racket", 65: "wheelchair_push_walk",
    66: "wheelchair_push_run", 67: "meditation", 68: "para_sport",
    69: "disc_golf", 70: "team_sport", 71: "cricket", 72: "rugby",
    73: "hockey", 74: "lacrosse", 75: "volleyball", 76: "water_tubing",
    77: "wakesurfing", 78: "water_sport", 79: "archery", 80: "mixed_martial_arts",
    81: "motor_sports", 82: "snorkeling", 83: "dance", 84: "jump_rope",
    85: "pool_apnea", 86: "mobility", 87: "geocaching",
}

_FIT_SUB_SPORT_NAMES = {
    0: "generic", 1: "treadmill", 2: "street", 3: "trail", 4: "track",
    5: "spin", 6: "indoor_cycling", 7: "road", 8: "mountain", 9: "downhill",
    10: "recumbent", 11: "cyclocross", 12: "hand_cycling", 13: "track_cycling",
    14: "indoor_rowing", 15: "elliptical", 16: "stair_climbing", 17: "lap_swimming",
    18: "open_water", 19: "flexibility_training", 20: "strength_training", 21: "warm_up",
    22: "match", 23: "exercise", 24: "challenge", 25: "indoor_skiing",
    26: "cardio_training", 27: "indoor_walking", 28: "e_bike_fitness", 29: "bmx",
    30: "casual_walking", 31: "speed_walking", 32: "bike_to_run_transition",
    33: "run_to_bike_transition", 34: "swim_to_bike_transition", 35: "atv",
    36: "motocross", 37: "backcountry", 38: "resort", 39: "rc_drone",
    40: "wingsuit", 41: "whitewater", 42: "skate_skiing", 43: "yoga",
    44: "pilates", 45: "indoor_running", 46: "gravel_cycling", 47: "e_bike_mountain",
    48: "commuting", 49: "mixed_surface", 50: "navigate", 51: "track_me",
    52: "map", 53: "single_gas_diving", 54: "multi_gas_diving", 55: "gauge_diving",
    56: "apnea_diving", 57: "apnea_hunting", 58: "virtual_activity", 59: "obstacle",
    62: "breathing", 63: "ccr_diving", 65: "sail_race", 66: "expedition",
    67: "ultra", 68: "indoor_climbing", 69: "bouldering", 70: "hiit",
    71: "indoor_grinding", 72: "hunting_with_dogs", 73: "amrap", 74: "emom",
    75: "tabata", 77: "esport", 78: "triathlon", 79: "duathlon", 80: "brick",
    81: "swim_run", 82: "adventure_race", 83: "trucker_workout",
}

_ACTIVITY_FILE_TYPE_NAMES = {0: "manual", 1: "auto_multi_sport"}


@dataclass(frozen=True, slots=True)
class ActivitySummary:
    """Semantic session/lap/activity aggregate from a standard FIT file."""

    kind: str
    global_message_number: int
    timestamp_garmin: int | None
    timestamp_utc: datetime | None
    values: tuple[tuple[str, Any], ...]

    def value(self, name: str, default: Any = None) -> Any:
        for key, value in self.values:
            if key == name:
                return value
        return default


def _activity_summary_from_record(record: FitDataRecord) -> ActivitySummary | None:
    message = record.global_message_number
    if message == FIT_SESSION_GLOBAL_MESSAGE:
        avg_speed = record.value(124)
        if avg_speed is None:
            avg_speed = record.value(14)
        max_speed = record.value(125)
        if max_speed is None:
            max_speed = record.value(15)
        values = [
            ("start_time_garmin", record.value(2)),
            ("sport", _enum_fit_value(record.value(5), _FIT_SPORT_NAMES)),
            ("sub_sport", _enum_fit_value(record.value(6), _FIT_SUB_SPORT_NAMES)),
            ("total_elapsed_time_s", _scaled_fit_value(record.value(7), 1000.0)),
            ("total_timer_time_s", _scaled_fit_value(record.value(8), 1000.0)),
            ("total_distance_m", _scaled_fit_value(record.value(9), 100.0)),
            ("total_cycles", record.value(10)),
            ("total_calories_kcal", record.value(11)),
            ("average_speed_mps", _scaled_fit_value(avg_speed, 1000.0)),
            ("maximum_speed_mps", _scaled_fit_value(max_speed, 1000.0)),
            ("average_heart_rate_bpm", record.value(16)),
            ("maximum_heart_rate_bpm", record.value(17)),
            ("average_cadence_rpm", record.value(18)),
            ("maximum_cadence_rpm", record.value(19)),
            ("average_power_w", record.value(20)),
            ("maximum_power_w", record.value(21)),
            ("total_ascent_m", record.value(22)),
            ("total_descent_m", record.value(23)),
            ("training_effect", _scaled_fit_value(record.value(24), 10.0)),
            ("num_laps", record.value(26)),
            ("total_moving_time_s", _scaled_fit_value(record.value(59), 1000.0)),
            ("average_temperature_c", record.value(57)),
        ]
        kind = "session"
    elif message == FIT_LAP_GLOBAL_MESSAGE:
        values = [
            ("start_time_garmin", record.value(2)),
            ("sport", _enum_fit_value(record.value(25), _FIT_SPORT_NAMES)),
            ("total_elapsed_time_s", _scaled_fit_value(record.value(7), 1000.0)),
            ("total_timer_time_s", _scaled_fit_value(record.value(8), 1000.0)),
            ("total_distance_m", _scaled_fit_value(record.value(9), 100.0)),
            ("total_cycles", record.value(10)),
            ("total_calories_kcal", record.value(11)),
            ("average_speed_mps", _scaled_fit_value(record.value(13), 1000.0)),
            ("maximum_speed_mps", _scaled_fit_value(record.value(14), 1000.0)),
            ("average_heart_rate_bpm", record.value(15)),
            ("maximum_heart_rate_bpm", record.value(16)),
            ("average_cadence_rpm", record.value(17)),
            ("maximum_cadence_rpm", record.value(18)),
            ("average_power_w", record.value(19)),
            ("maximum_power_w", record.value(20)),
            ("total_ascent_m", record.value(21)),
            ("total_descent_m", record.value(22)),
            ("total_moving_time_s", _scaled_fit_value(record.value(52), 1000.0)),
        ]
        kind = "lap"
    elif message == FIT_ACTIVITY_GLOBAL_MESSAGE:
        values = [
            ("total_timer_time_s", _scaled_fit_value(record.value(0), 1000.0)),
            ("num_sessions", record.value(1)),
            ("activity_type", _enum_fit_value(record.value(2), _ACTIVITY_FILE_TYPE_NAMES)),
            ("local_timestamp", record.value(5)),
        ]
        kind = "activity"
    else:
        return None
    return ActivitySummary(
        kind,
        message,
        record.timestamp,
        fit_timestamp_utc(record.timestamp),
        _present_values(values),
    )


def extract_activity_summaries(data: bytes) -> tuple[ActivitySummary, ...]:
    """Extract Session, Lap and Activity aggregates from a verified FIT file."""
    return tuple(
        summary
        for record in parse_fit_records(data)
        if (summary := _activity_summary_from_record(record)) is not None
    )


def activity_summary_dict(summary: ActivitySummary) -> dict[str, Any]:
    return {
        "kind": summary.kind,
        "global_message_number": summary.global_message_number,
        "timestamp_garmin": summary.timestamp_garmin,
        "timestamp_utc": summary.timestamp_utc.isoformat() if summary.timestamp_utc is not None else None,
        "values": {name: _fit_json_value(value) for name, value in summary.values},
    }


@dataclass(frozen=True, slots=True)
class WellnessSample:
    """Semantic projection of a standard FIT wellness/monitoring message."""

    kind: str
    global_message_number: int
    timestamp_garmin: int | None
    timestamp_utc: datetime | None
    values: tuple[tuple[str, Any], ...]

    def value(self, name: str, default: Any = None) -> Any:
        for key, value in self.values:
            if key == name:
                return value
        return default


def _scaled_fit_value(value: Any, scale: float, offset: float = 0.0) -> Any:
    if isinstance(value, (int, float)):
        return float(value) / scale - offset
    if isinstance(value, tuple):
        return tuple(
            None if item is None else float(item) / scale - offset
            if isinstance(item, (int, float)) else item
            for item in value
        )
    return None


def _enum_fit_value(value: Any, names: dict[int, str]) -> Any:
    if isinstance(value, int):
        return names.get(value, value)
    return value


def _present_values(values: list[tuple[str, Any]]) -> tuple[tuple[str, Any], ...]:
    return tuple((name, value) for name, value in values if value is not None)


def _wellness_from_record(record: FitDataRecord) -> WellnessSample | None:
    message = record.global_message_number
    values: list[tuple[str, Any]]
    timestamp = record.timestamp

    if message == FIT_WEIGHT_SCALE_GLOBAL_MESSAGE:
        values = [
            ("weight_kg", _scaled_fit_value(record.value(0), 100.0)),
            ("body_fat_percent", _scaled_fit_value(record.value(1), 100.0)),
            ("hydration_percent", _scaled_fit_value(record.value(2), 100.0)),
            ("visceral_fat_mass_kg", _scaled_fit_value(record.value(3), 100.0)),
            ("bone_mass_kg", _scaled_fit_value(record.value(4), 100.0)),
            ("muscle_mass_kg", _scaled_fit_value(record.value(5), 100.0)),
            ("basal_metabolic_rate_kcal_per_day", _scaled_fit_value(record.value(7), 4.0)),
            ("physique_rating", record.value(8)),
            ("active_metabolic_rate_kcal_per_day", _scaled_fit_value(record.value(9), 4.0)),
            ("metabolic_age_years", record.value(10)),
            ("visceral_fat_rating", record.value(11)),
            ("bmi_kg_per_m2", _scaled_fit_value(record.value(13), 10.0)),
        ]
        kind = "weight_scale"
    elif message == FIT_BLOOD_PRESSURE_GLOBAL_MESSAGE:
        values = [
            ("systolic_mmhg", record.value(0)),
            ("diastolic_mmhg", record.value(1)),
            ("mean_arterial_pressure_mmhg", record.value(2)),
            ("map_three_sample_mean_mmhg", record.value(3)),
            ("map_morning_mmhg", record.value(4)),
            ("map_evening_mmhg", record.value(5)),
            ("heart_rate_bpm", record.value(6)),
            ("heart_rate_type", _enum_fit_value(record.value(7), _HR_TYPE_NAMES)),
            ("status", _enum_fit_value(record.value(8), _BP_STATUS_NAMES)),
        ]
        kind = "blood_pressure"
    elif message == FIT_MONITORING_GLOBAL_MESSAGE:
        values = [
            ("device_index", record.value(0)),
            ("calories_kcal", record.value(1)),
            ("distance_m", _scaled_fit_value(record.value(2), 100.0)),
            ("cycles", _scaled_fit_value(record.value(3), 2.0)),
            ("active_time_s", _scaled_fit_value(record.value(4), 1000.0)),
            ("activity_type", _enum_fit_value(record.value(5), _ACTIVITY_TYPE_NAMES)),
            ("activity_subtype", _enum_fit_value(record.value(6), _ACTIVITY_SUBTYPE_NAMES)),
            ("activity_level", record.value(7)),
            ("local_timestamp", record.value(11)),
            ("temperature_c", _scaled_fit_value(record.value(12), 100.0)),
            ("temperature_min_c", _scaled_fit_value(record.value(14), 100.0)),
            ("temperature_max_c", _scaled_fit_value(record.value(15), 100.0)),
            ("activity_time_min", record.value(16)),
            ("active_calories_kcal", record.value(19)),
            ("heart_rate_bpm", record.value(27)),
            ("intensity", _scaled_fit_value(record.value(28), 10.0)),
            ("duration_min", record.value(29)),
            ("duration_s", record.value(30)),
            ("ascent_m", _scaled_fit_value(record.value(31), 1000.0)),
            ("descent_m", _scaled_fit_value(record.value(32), 1000.0)),
            ("moderate_activity_min", record.value(33)),
            ("vigorous_activity_min", record.value(34)),
            ("pushes", record.value(41)),
        ]
        kind = "monitoring"
    elif message == FIT_MONITORING_INFO_GLOBAL_MESSAGE:
        values = [
            ("local_timestamp", record.value(0)),
            ("activity_type", _enum_fit_value(record.value(1), _ACTIVITY_TYPE_NAMES)),
            ("cycles_to_distance_m_per_cycle", _scaled_fit_value(record.value(3), 5000.0)),
            ("cycles_to_calories_kcal_per_cycle", _scaled_fit_value(record.value(4), 5000.0)),
            ("resting_metabolic_rate_kcal_per_day", record.value(5)),
        ]
        kind = "monitoring_info"
    elif message == FIT_HRV_GLOBAL_MESSAGE:
        rr = record.value(0)
        if isinstance(rr, tuple):
            rr_ms = tuple(float(value) if isinstance(value, (int, float)) else None for value in rr)
        elif isinstance(rr, (int, float)):
            rr_ms = (float(rr),)
        else:
            rr_ms = None
        values = [("rr_intervals_ms", rr_ms)]
        kind = "hrv"
    elif message == FIT_HR_GLOBAL_MESSAGE:
        values = [
            ("fractional_timestamp_s", _scaled_fit_value(record.value(0), 32768.0)),
            ("time256_s", _scaled_fit_value(record.value(1), 256.0)),
            ("filtered_bpm", record.value(6)),
            ("event_timestamp_s", _scaled_fit_value(record.value(9), 1024.0)),
        ]
        kind = "heart_rate"
    elif message == FIT_MONITORING_HR_GLOBAL_MESSAGE:
        values = [
            ("resting_heart_rate_bpm", record.value(0)),
            ("current_day_resting_heart_rate_bpm", record.value(1)),
        ]
        kind = "monitoring_heart_rate"
    elif message == FIT_STRESS_LEVEL_GLOBAL_MESSAGE:
        stress_timestamp = record.value(1)
        if isinstance(stress_timestamp, int):
            timestamp = stress_timestamp
        values = [("stress_level", record.value(0))]
        kind = "stress"
    elif message == FIT_SPO2_GLOBAL_MESSAGE:
        values = [
            ("spo2_percent", record.value(0)),
            ("confidence", record.value(1)),
            ("mode", _enum_fit_value(record.value(2), _SPO2_MODE_NAMES)),
        ]
        kind = "spo2"
    elif message == FIT_SLEEP_LEVEL_GLOBAL_MESSAGE:
        values = [("sleep_level", _enum_fit_value(record.value(0), _SLEEP_LEVEL_NAMES))]
        kind = "sleep_level"
    elif message == FIT_RESPIRATION_RATE_GLOBAL_MESSAGE:
        values = [("respiration_rate_breaths_per_min", _scaled_fit_value(record.value(0), 100.0))]
        kind = "respiration_rate"
    elif message == FIT_BODY_BATTERY_GLOBAL_MESSAGE:
        values = [
            ("processing_interval_s", record.value(0)),
            ("body_battery_percent", record.value(1)),
            ("charged", record.value(2)),
            ("uncharged", record.value(3)),
        ]
        kind = "body_battery"
    else:
        return None

    return WellnessSample(
        kind,
        message,
        timestamp,
        fit_timestamp_utc(timestamp),
        _present_values(values),
    )


def extract_wellness_samples(data: bytes) -> tuple[WellnessSample, ...]:
    """Extract standard monitoring, sleep, HRV, stress, SpO2 and related samples."""
    return tuple(
        sample
        for record in parse_fit_records(data)
        if (sample := _wellness_from_record(record)) is not None
    )


def wellness_sample_dict(sample: WellnessSample) -> dict[str, Any]:
    return {
        "kind": sample.kind,
        "global_message_number": sample.global_message_number,
        "timestamp_garmin": sample.timestamp_garmin,
        "timestamp_utc": sample.timestamp_utc.isoformat() if sample.timestamp_utc is not None else None,
        "values": {name: _fit_json_value(value) for name, value in sample.values},
    }

def _fit_json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"hex": value.hex()}
    if isinstance(value, tuple):
        return [_fit_json_value(item) for item in value]
    return value


def fit_record_dict(record: FitDataRecord) -> dict[str, Any]:
    """Return a JSON-ready generic FIT record without dropping unknown fields."""
    return {
        "global_message_number": record.global_message_number,
        "message_name": record.message_name,
        "local_message_number": record.local_message_number,
        "timestamp_garmin": record.timestamp,
        "timestamp_utc": fit_timestamp_utc(record.timestamp).isoformat() if record.timestamp is not None else None,
        "fields": [
            {
                "number": field.number,
                "base_type": field.base_type,
                "value": _fit_json_value(field.value),
                "raw_hex": field.raw.hex(),
            }
            for field in record.fields
        ],
        "developer_data_hex": record.developer_data.hex(),
    }


def fit_semantic_summary(
    data: bytes,
    *,
    include_activity_samples: bool = False,
    include_activity_summaries: bool = False,
    include_wellness_samples: bool = False,
    include_records: bool = False,
) -> dict[str, Any]:
    """Build an application-facing summary from a verified FIT object."""
    inspection = inspect_fit(data)
    records = parse_fit_records(data)
    samples = extract_activity_samples(data)
    activity_summaries = extract_activity_summaries(data)
    wellness = extract_wellness_samples(data)
    counts: dict[str, int] = {}
    for record in records:
        key = record.message_name or str(record.global_message_number)
        counts[key] = counts.get(key, 0) + 1
    summary: dict[str, Any] = {
        "file_type": int(inspection.file_type_raw) if inspection.file_type_raw is not None else None,
        "record_count": len(records),
        "message_counts": counts,
        "activity_sample_count": len(samples),
        "activity_summary_count": len(activity_summaries),
        "wellness_sample_count": len(wellness),
    }
    if include_activity_samples:
        summary["activity_samples"] = [activity_sample_dict(sample) for sample in samples]
    if include_activity_summaries:
        summary["activity_summaries"] = [activity_summary_dict(item) for item in activity_summaries]
    if include_wellness_samples:
        summary["wellness_samples"] = [wellness_sample_dict(sample) for sample in wellness]
    if include_records:
        summary["records"] = [fit_record_dict(record) for record in records]
    return summary
