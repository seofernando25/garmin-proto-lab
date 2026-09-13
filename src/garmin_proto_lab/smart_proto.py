"""Independent codecs for the GDI Smart protobuf envelope and Core/GNCS capabilities.

The APK uses an empty extendable ``GDI.Proto.Smart.Smart`` message.  Services
are protobuf extensions: Core is field 13 and GNCS service traffic is field 49.
This module models only the v1 interoperability subset and preserves the raw
wire representation of anything it does not understand.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .protobuf_wire import (
    ProtobufWireError,
    WireField,
    WireType,
    encode_bytes,
    encode_message,
    encode_string,
    encode_uint,
    last_bytes,
    last_varint,
    parse_fields,
)

SMART_CORE_EXTENSION = 13
SMART_GNCS_SERVICE_EXTENSION = 49
CORE_FEATURE_CAPABILITIES_REQUEST = 8
CORE_FEATURE_CAPABILITIES_RESPONSE = 9
CORE_CONNECTION_READY_NOTIFICATION = 14
FEATURE_CAPABILITIES_GNCS_EXTENSION = 12


class NotificationDisabledReason(IntEnum):
    LOW_RAM_MOBILE_DEVICE = 1
    PERMISSION_NOT_GRANTED = 2
    SERVICE_NOT_BOUND = 3


class GuidStatus(IntEnum):
    UNSET = 0
    MATCH = 1
    NO_MATCH = 2


@dataclass(frozen=True, slots=True)
class SmartMessage:
    fields: tuple[WireField, ...]

    @classmethod
    def parse(cls, data: bytes) -> "SmartMessage":
        return cls(parse_fields(data))

    def extension(self, field_number: int) -> bytes | None:
        return last_bytes(self.fields, field_number)

    @property
    def extension_numbers(self) -> tuple[int, ...]:
        return tuple(field.number for field in self.fields)


def build_smart_extension(field_number: int, payload: bytes) -> bytes:
    return encode_message(field_number, payload)


def semantic_version_to_int(version: str) -> int:
    """Encode ``major.minor.patch[-suffix]`` as Garmin's GNCS version integer."""
    core = version.split("-", 1)[0]
    parts = core.split(".")
    if len(parts) != 3:
        raise ValueError("semantic version must contain major.minor.patch")
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("semantic version components must be integers") from exc
    if not all(0 <= value <= 0xFF for value in (major, minor, patch)):
        raise ValueError("semantic version components must fit one byte")
    return (major << 16) | (minor << 8) | patch


@dataclass(frozen=True, slots=True)
class GncsCapabilitiesAdvertisement:
    np_version: int
    notification_disabled_reason: NotificationDisabledReason | int | None = None
    default_messaging_app_id: str = ""
    default_dialer_app_id: str = ""

    def encode(self) -> bytes:
        if not 0 <= self.np_version <= 0xFFFFFFFF:
            raise ValueError("np_version must fit uint32")
        out = bytearray(encode_uint(1, self.np_version))
        if self.notification_disabled_reason is not None:
            out += encode_uint(2, int(self.notification_disabled_reason))
        # Garmin's handler explicitly sets both strings even when empty, which
        # matters for proto2 field presence.
        out += encode_string(3, self.default_messaging_app_id)
        out += encode_string(4, self.default_dialer_app_id)
        return bytes(out)


@dataclass(frozen=True, slots=True)
class FeatureCapabilitiesRequest:
    gncs: GncsCapabilitiesAdvertisement | None = None
    garmin_guid: bytes | None = None
    client_version: int | None = None
    display_name: str | None = None

    def encode(self) -> bytes:
        out = bytearray()
        if self.garmin_guid is not None:
            out += encode_bytes(1, self.garmin_guid)
        if self.client_version is not None:
            out += encode_uint(2, self.client_version)
        if self.display_name is not None:
            out += encode_string(3, self.display_name)
        if self.gncs is not None:
            out += encode_message(FEATURE_CAPABILITIES_GNCS_EXTENSION, self.gncs.encode())
        return bytes(out)


def build_feature_capabilities_request(request: FeatureCapabilitiesRequest) -> bytes:
    core = encode_message(CORE_FEATURE_CAPABILITIES_REQUEST, request.encode())
    return build_smart_extension(SMART_CORE_EXTENSION, core)


@dataclass(frozen=True, slots=True)
class GncsCapabilitiesResponse:
    nc_version: int | None
    support_blocked_apps: bool | None
    raw_fields: tuple[WireField, ...]

    @property
    def modified_notifications_after_added(self) -> bool:
        return self.nc_version is not None and self.nc_version >= 1

    @classmethod
    def parse(cls, data: bytes) -> "GncsCapabilitiesResponse":
        fields = parse_fields(data)
        nc_version = last_varint(fields, 1)
        blocked = last_varint(fields, 2)
        return cls(nc_version, None if blocked is None else bool(blocked), fields)


@dataclass(frozen=True, slots=True)
class FeatureCapabilitiesResponse:
    guid_status: GuidStatus | int | None
    version: int | None
    gncs: GncsCapabilitiesResponse | None
    raw_fields: tuple[WireField, ...]


def parse_feature_capabilities_response(smart_bytes: bytes) -> FeatureCapabilitiesResponse:
    smart = SmartMessage.parse(smart_bytes)
    core = smart.extension(SMART_CORE_EXTENSION)
    if core is None:
        raise ProtobufWireError("Smart message has no Core extension")
    core_fields = parse_fields(core)
    response = last_bytes(core_fields, CORE_FEATURE_CAPABILITIES_RESPONSE)
    if response is None:
        raise ProtobufWireError("Core service has no feature-capabilities response")
    fields = parse_fields(response)
    raw_guid = last_varint(fields, 1)
    if raw_guid is None:
        guid: GuidStatus | int | None = None
    else:
        try:
            guid = GuidStatus(raw_guid)
        except ValueError:
            guid = raw_guid
    version = last_varint(fields, 2)
    raw_gncs = last_bytes(fields, FEATURE_CAPABILITIES_GNCS_EXTENSION)
    gncs = GncsCapabilitiesResponse.parse(raw_gncs) if raw_gncs is not None else None
    return FeatureCapabilitiesResponse(guid, version, gncs, fields)


def is_connection_ready_notification(smart_bytes: bytes) -> bool:
    smart = SmartMessage.parse(smart_bytes)
    core = smart.extension(SMART_CORE_EXTENSION)
    if core is None:
        return False
    fields = parse_fields(core)
    return any(
        field.number == CORE_CONNECTION_READY_NOTIFICATION
        and field.wire_type is WireType.LENGTH_DELIMITED
        for field in fields
    )


def smart_has_known_extension(smart_bytes: bytes) -> bool:
    smart = SmartMessage.parse(smart_bytes)
    return any(number in {SMART_CORE_EXTENSION, SMART_GNCS_SERVICE_EXTENSION} for number in smart.extension_numbers)
