"""Static MultiLink control-plane codecs used by next-generation FileAccess.

MultiLink multiplexes services over a Garmin GATT service. Command packets start
with byte 0 and use little-endian client/service identifiers. Service data uses
its assigned handle as the first raw packet byte (or the MLR reliable header).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID


class MultiLinkError(ValueError):
    pass


GARMIN_MULTILINK_SUFFIX = "-667b-11e3-949a-0800200c9a66"
MULTILINK_SERVICE_UUID = UUID("6a4e2800-667b-11e3-949a-0800200c9a66")
MULTILINK_INFO_CHARACTERISTIC_UUID = UUID("6a4e2803-667b-11e3-949a-0800200c9a66")
MULTILINK_PRIMARY_CHARACTERISTICS = tuple(
    UUID(f"6a4e{value:04x}{GARMIN_MULTILINK_SUFFIX}") for value in range(0x2810, 0x281A)
)
MULTILINK_PAIRED_CHARACTERISTICS = tuple(
    UUID(f"6a4e{value:04x}{GARMIN_MULTILINK_SUFFIX}") for value in range(0x2820, 0x282A)
)

# Application-defined ID used by this open-source client. The little-endian
# wire bytes spell ``GPLAB001`` so hardware traces are visibly independent of
# Garmin Connect. It is not a Garmin-recovered identifier and may be overridden.
DEFAULT_INDEPENDENT_CLIENT_ID = int.from_bytes(b"GPLAB001", "little")

REGISTRATION_SERVICE_ID = 4
REGISTER_FLAG_REQUEST_RELIABLE = 0x02
REGISTER_RESPONSE_FLAG_RELIABLE = 0x01
FILE_TRANSFER_PIPE_SERVICE_IDS = (0x2018, 0x4018, 0x6018, 0x8018, 0xA018, 0xC018, 0xE018)

COMMAND_REGISTER_REQUEST = 0
COMMAND_REGISTER_RESPONSE = 1
COMMAND_CLOSE_HANDLE_REQUEST = 2
COMMAND_CLOSE_HANDLE_RESPONSE = 3
COMMAND_INVALID_HANDLE = 4
COMMAND_CLOSE_ALL_REQUEST = 5
COMMAND_CLOSE_ALL_RESPONSE = 6
COMMAND_UNKNOWN = 0xFF


class RegisterStatus(IntEnum):
    SUCCESS = 0
    INVALID_SERVICE_ID = 1
    PENDING_AUTH = 2
    ALREADY_IN_USE = 3
    REJECTED = 4


class CloseStatus(IntEnum):
    SUCCESS = 0
    INVALID_HANDLE = 1
    NO_CONNECTION = 2


def _u64(value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise MultiLinkError(f"{label} must fit uint64")
    return value.to_bytes(8, "little")


def _u16(value: int, label: str) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise MultiLinkError(f"{label} must fit uint16")
    return value.to_bytes(2, "little")


def _enum(enum_type, value: int):
    try:
        return enum_type(value)
    except ValueError:
        return value


def paired_characteristic(primary: UUID) -> UUID:
    """Return the statically paired 0x282x characteristic for a 0x281x one."""
    text = str(primary).lower()
    prefix = text.split("-", 1)[0]
    if not prefix.startswith("6a4e") or len(prefix) != 8:
        return primary
    try:
        short = int(prefix[4:], 16)
    except ValueError:
        return primary
    if not 0x2810 <= short <= 0x2819:
        return primary
    return UUID(f"6a4e{short + 0x10:04x}{GARMIN_MULTILINK_SUFFIX}")


def candidate_characteristic_pairs(characteristics: set[UUID] | frozenset[UUID]) -> tuple[tuple[UUID, UUID], ...]:
    """Return notify/write pairs in the same order used by the static scan.

    Garmin first selects one of 0x2810..0x2819. It maps that UUID to 0x2820..
    0x2829 for the paired characteristic when present; otherwise it uses the
    same characteristic for both directions.
    """
    out: list[tuple[UUID, UUID]] = []
    for primary in MULTILINK_PRIMARY_CHARACTERISTICS:
        if primary not in characteristics:
            continue
        paired = paired_characteristic(primary)
        out.append((primary, paired if paired in characteristics else primary))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class RegisterServiceRequest:
    connection_id: int
    service_id: int
    request_reliable: bool = False

    def encode(self) -> bytes:
        flags = REGISTER_FLAG_REQUEST_RELIABLE if self.request_reliable else 0
        return b"\x00\x00" + _u64(self.connection_id, "connection_id") + _u16(self.service_id, "service_id") + bytes((flags,))


@dataclass(frozen=True, slots=True)
class RegisterServiceResponse:
    connection_id: int
    service_id: int
    status: RegisterStatus | int
    handle: int | None = None
    flags: int = 0
    revision: int = 0
    alternate_characteristic: UUID | None = None
    raw: bytes = b""

    @property
    def reliable(self) -> bool:
        return bool(self.flags & REGISTER_RESPONSE_FLAG_RELIABLE)

    @classmethod
    def parse(cls, data: bytes) -> "RegisterServiceResponse":
        raw = bytes(data)
        if len(raw) < 13 or raw[0:2] != b"\x00\x01":
            raise MultiLinkError("invalid MultiLink register response")
        connection_id = int.from_bytes(raw[2:10], "little")
        service_id = int.from_bytes(raw[10:12], "little")
        status_value = raw[12]
        status = _enum(RegisterStatus, status_value)
        if status_value == int(RegisterStatus.SUCCESS):
            if len(raw) < 14:
                raise MultiLinkError("successful register response is missing handle")
            handle = raw[13]
            flags = raw[14] if len(raw) >= 15 else 0
            revision = raw[15] if len(raw) >= 16 else 0
            return cls(connection_id, service_id, status, handle, flags, revision, None, raw)
        alternate = None
        if status_value == int(RegisterStatus.ALREADY_IN_USE) and len(raw) >= 15:
            short = int.from_bytes(raw[13:15], "little")
            alternate = UUID(f"6a4e{short:04x}{GARMIN_MULTILINK_SUFFIX}")
        return cls(connection_id, service_id, status, None, 0, 0, alternate, raw)


@dataclass(frozen=True, slots=True)
class CloseHandleRequest:
    connection_id: int
    service_id: int
    handle: int

    def encode(self) -> bytes:
        if not 0 <= self.handle <= 0xFF:
            raise MultiLinkError("handle must fit uint8")
        return b"\x00\x02" + _u64(self.connection_id, "connection_id") + _u16(self.service_id, "service_id") + bytes((self.handle,))


@dataclass(frozen=True, slots=True)
class CloseHandleResponse:
    connection_id: int
    service_id: int
    handle: int
    status: CloseStatus | int
    raw: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "CloseHandleResponse":
        raw = bytes(data)
        if len(raw) < 14 or raw[0:2] != b"\x00\x03":
            raise MultiLinkError("invalid MultiLink close-handle response")
        return cls(
            int.from_bytes(raw[2:10], "little"),
            int.from_bytes(raw[10:12], "little"),
            raw[12],
            _enum(CloseStatus, raw[13]),
            raw,
        )


@dataclass(frozen=True, slots=True)
class CloseAllRequest:
    connection_id: int

    def encode(self) -> bytes:
        return b"\x00\x05" + _u64(self.connection_id, "connection_id") + b"\x00\x00"


@dataclass(frozen=True, slots=True)
class CloseAllResponse:
    connection_id: int
    status: CloseStatus | int
    raw: bytes = b""

    @classmethod
    def parse(cls, data: bytes) -> "CloseAllResponse":
        raw = bytes(data)
        if len(raw) < 13 or raw[0:2] != b"\x00\x06":
            raise MultiLinkError("invalid MultiLink close-all response")
        return cls(int.from_bytes(raw[2:10], "little"), _enum(CloseStatus, raw[12]), raw)


@dataclass(frozen=True, slots=True)
class InvalidHandleNotification:
    handle: int
    raw: bytes

    @classmethod
    def parse(cls, data: bytes) -> "InvalidHandleNotification":
        raw = bytes(data)
        if len(raw) < 13 or raw[0:2] != b"\x00\x04":
            raise MultiLinkError("invalid MultiLink invalid-handle notification")
        return cls(raw[12], raw)


def parse_control_message(data: bytes):
    raw = bytes(data)
    if len(raw) < 2 or raw[0] != 0:
        raise MultiLinkError("not a MultiLink control message")
    command = raw[1]
    if command == COMMAND_REGISTER_RESPONSE:
        return RegisterServiceResponse.parse(raw)
    if command == COMMAND_CLOSE_HANDLE_RESPONSE:
        return CloseHandleResponse.parse(raw)
    if command == COMMAND_INVALID_HANDLE:
        return InvalidHandleNotification.parse(raw)
    if command == COMMAND_CLOSE_ALL_RESPONSE:
        return CloseAllResponse.parse(raw)
    raise MultiLinkError(f"unsupported MultiLink control command {command}")
