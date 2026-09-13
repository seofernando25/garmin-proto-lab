"""Independent wire codecs for Garmin GDI FileAccess protobuf messages.

This is the next-generation file API gated by legacy Configuration flag 90.
Control messages travel inside Smart extension field 43.  Actual negotiated file
bytes use a separate reliable MultiLink transport pipe; this module deliberately
models only the protobuf control plane.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from .protobuf_wire import (
    ProtobufWireError,
    WireField,
    WireType,
    decode_sint32,
    encode_bool,
    encode_bytes,
    encode_fixed64,
    encode_message,
    encode_sint32,
    encode_string,
    encode_uint,
    last_bytes,
    last_fixed64,
    last_varint,
    parse_fields,
)
from .smart_proto import SmartMessage, build_smart_extension

FILE_ACCESS_CONFIGURATION_FLAG = 90
FEATURE_CAPABILITIES_FILE_ACCESS_EXTENSION = 16
SMART_FILE_ACCESS_EXTENSION = 43

SERVICE_PULL_ITEM_REQUEST = 1
SERVICE_PULL_ITEM_RESPONSE = 2
SERVICE_PUSH_ITEM_REQUEST = 3
SERVICE_PUSH_ITEM_RESPONSE = 4
SERVICE_TRANSFER_STATUS_REQUEST = 5
SERVICE_PRIORITY_UPDATE_REQUEST = 6
SERVICE_PRIORITY_UPDATE_RESPONSE = 7
SERVICE_ITEM_LIST_REQUEST = 9
SERVICE_ITEM_LIST_RESPONSE = 10
SERVICE_ITEM_LIST_CANCEL_NOTIFICATION = 11
SERVICE_ITEM_ADDED_NOTIFICATION = 12
SERVICE_DELETE_ITEM_REQUEST = 13
SERVICE_DELETE_ITEM_RESPONSE = 14
SERVICE_MODIFY_FLAGS_REQUEST = 15
SERVICE_MODIFY_FLAGS_RESPONSE = 16
SERVICE_ITEM_UPDATED_NOTIFICATION = 17
SERVICE_CANCEL_TRANSFER_REQUEST = 18
SERVICE_CANCEL_TRANSFER_RESPONSE = 19
SERVICE_RESOURCE_UPDATE_NOTIFICATION = 20
SERVICE_TRANSFER_STATUS_RESPONSE = 21
SERVICE_SYNC_BUTTON_NOTIFICATION = 22
SERVICE_SOFTWARE_UPDATE_PART_NUMBER_REQUEST = 23
SERVICE_SOFTWARE_UPDATE_PART_NUMBER_RESPONSE = 24
SERVICE_GET_ITEM_CHECKSUM_REQUEST = 25
SERVICE_GET_ITEM_CHECKSUM_RESPONSE = 26


class DataTypeFormat(IntEnum):
    GDXML_DATA_TYPE = 0
    SOFTWARE_UPDATE = 1
    SPECIAL_CASE = 2
    GDXML_FULL_FILE = 3
    DATA_XFER = 4
    GDXML_UPDATE_FILE = 5


class DataTypeSpecialCase(IntEnum):
    COUNTER_BYTE_TEST = 1


class ItemUrgency(IntEnum):
    IMMEDIATE = 0
    NORMAL = 1
    SLOW = 2


class ItemAccessResult(IntEnum):
    SUCCESS = 0
    ITEM_TYPE_NOT_SUPPORTED = 1
    NO_SUPPORTED_TRANSPORT = 2
    NO_AVAILABLE_RESOURCES = 3
    INTERNAL_ERROR = 4
    INVALID_PARAMS = 5
    INVALID_OFFSET = 6
    AMBIGUOUS_DATA_TYPE = 7
    UNKNOWN_ITEM = 8
    NO_SPACE = 9
    DATA_TYPE_ERROR = 10
    CONFLICTING_PUSH_REQUEST = 11


class TransportProtocol(IntEnum):
    MULTILINK_TRANSPORT_PIPE = 0


class TransferDirection(IntEnum):
    PUSH = 0
    PULL = 1


class MlrPipeDirection(IntEnum):
    READ = 0
    WRITE = 1


class TransferPriorityLevel(IntEnum):
    LOWEST_START = 0
    LOWEST_END = 9
    UTILITY_START = 10
    UTILITY_END = 19
    STANDARD_START = 20
    STANDARD_END = 29
    USER_INITIATED_START = 30
    USER_INITIATED_END = 39
    HIGHEST_START = 40
    HIGHEST_END = 49


class ItemListStatus(IntEnum):
    SUCCESS = 0
    FAIL_UNKNOWN_SESSION_ID = 1
    FAIL_TOO_MANY_EXCLUDE_FLAGS = 2
    FAIL_TOO_MANY_GET_FLAGS = 3
    FAIL_OTHER = 4
    FAIL_TOO_MANY_INCLUDE_TYPES = 5
    FAIL_INVALID_DATA_TYPE = 6


class ChecksumMethod(IntEnum):
    NOT_SUPPORTED = 0
    TRUNCATED_MD5 = 1


class TransferFailureReason(IntEnum):
    TRANSPORT_FAILED = 0
    HIGHER_PRIORITY_REQUEST_RECEIVED = 1
    ITEM_DELETED = 2
    CANCELLED_BY_CLIENT = 3
    CANCELLED_BY_DEVICE = 4
    COMPRESSION_FAILED = 5


def _enum(enum_type, value: int | None):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError:
        return value


def _stringsafe(raw: bytes | None) -> str | None:
    return None if raw is None else raw.decode("utf-8", errors="replace")


def _fixed64_field(fields: tuple[WireField, ...], number: int) -> int | None:
    return last_fixed64(fields, number)


def encode_uuid(value: UUID) -> bytes:
    raw = value.int
    most = (raw >> 64) & 0xFFFFFFFFFFFFFFFF
    least = raw & 0xFFFFFFFFFFFFFFFF
    return encode_fixed64(1, most) + encode_fixed64(2, least)


def parse_uuid(data: bytes) -> UUID:
    fields = parse_fields(data)
    most = _fixed64_field(fields, 1)
    least = _fixed64_field(fields, 2)
    if most is None or least is None:
        raise ProtobufWireError("FileAccess UUID requires most/least fixed64 fields")
    return UUID(int=((most << 64) | least))


@dataclass(frozen=True, slots=True)
class FileAccessCapabilities:
    server_push_file_bundle_support: bool | None = None
    software_update_part_number_request_support: bool | None = None
    server_file_checksum_method: ChecksumMethod | int | None = None
    checksum_max_file_size_byte: int | None = None
    custom_flag_support: bool | None = None
    raw_fields: tuple[WireField, ...] = ()

    def encode(self) -> bytes:
        out = bytearray()
        if self.server_push_file_bundle_support is not None:
            out += encode_bool(1, self.server_push_file_bundle_support)
        if self.software_update_part_number_request_support is not None:
            out += encode_bool(2, self.software_update_part_number_request_support)
        if self.server_file_checksum_method is not None:
            out += encode_uint(3, int(self.server_file_checksum_method))
        if self.checksum_max_file_size_byte is not None:
            out += encode_uint(4, self.checksum_max_file_size_byte)
        if self.custom_flag_support is not None:
            out += encode_bool(5, self.custom_flag_support)
        return bytes(out)

    @classmethod
    def parse(cls, data: bytes) -> "FileAccessCapabilities":
        fields = parse_fields(data)
        push = last_varint(fields, 1)
        sw = last_varint(fields, 2)
        checksum = last_varint(fields, 3)
        maximum = last_varint(fields, 4)
        custom = last_varint(fields, 5)
        return cls(
            None if push is None else bool(push),
            None if sw is None else bool(sw),
            _enum(ChecksumMethod, checksum),
            maximum,
            None if custom is None else bool(custom),
            fields,
        )


@dataclass(frozen=True, slots=True)
class FileDataType:
    format: DataTypeFormat | int | None = None
    gdxml_string: str | None = None
    gdxml_string_key: int | None = None
    gdxml_file_identifier: str | None = None
    software_update_path: str | None = None
    special_case: DataTypeSpecialCase | int | None = None
    update_part_number: str | None = None
    raw_fields: tuple[WireField, ...] = ()

    def encode(self) -> bytes:
        out = bytearray()
        if self.format is not None:
            out += encode_uint(1, int(self.format))
        if self.gdxml_string is not None:
            out += encode_string(2, self.gdxml_string)
        if self.gdxml_string_key is not None:
            out += encode_uint(3, self.gdxml_string_key)
        if self.gdxml_file_identifier is not None:
            out += encode_string(4, self.gdxml_file_identifier)
        if self.software_update_path is not None:
            out += encode_string(5, self.software_update_path)
        if self.special_case is not None:
            out += encode_uint(6, int(self.special_case))
        if self.update_part_number is not None:
            out += encode_string(7, self.update_part_number)
        return bytes(out)

    @classmethod
    def parse(cls, data: bytes) -> "FileDataType":
        fields = parse_fields(data)
        return cls(
            _enum(DataTypeFormat, last_varint(fields, 1)),
            _stringsafe(last_bytes(fields, 2)),
            last_varint(fields, 3),
            _stringsafe(last_bytes(fields, 4)),
            _stringsafe(last_bytes(fields, 5)),
            _enum(DataTypeSpecialCase, last_varint(fields, 6)),
            _stringsafe(last_bytes(fields, 7)),
            fields,
        )

    def resolved_name(self, string_table: dict[int, str] | None = None) -> str | None:
        if self.format is DataTypeFormat.GDXML_FULL_FILE:
            return "GDXml"
        if self.format is DataTypeFormat.DATA_XFER:
            return "AD_HOC_XFER"
        if self.format is DataTypeFormat.SPECIAL_CASE and self.special_case is DataTypeSpecialCase.COUNTER_BYTE_TEST:
            return "CounterByteTest"
        if self.format is not DataTypeFormat.GDXML_DATA_TYPE:
            return None
        if self.gdxml_string is not None:
            if string_table is not None and self.gdxml_string_key is not None:
                string_table[self.gdxml_string_key] = self.gdxml_string
            return self.gdxml_string
        if string_table is not None and self.gdxml_string_key is not None:
            return string_table.get(self.gdxml_string_key)
        return None


@dataclass(frozen=True, slots=True)
class FileItemReference:
    uid: UUID | None
    data_type: FileDataType | None
    data_size: int | None
    requested_flags: int | None = None
    transaction_id: int | None = None
    urgency: ItemUrgency | int | None = None
    modified_time: int | None = None
    raw_fields: tuple[WireField, ...] = ()

    def encode(self) -> bytes:
        out = bytearray()
        if self.uid is not None:
            out += encode_message(1, encode_uuid(self.uid))
        if self.data_type is not None:
            out += encode_message(2, self.data_type.encode())
        if self.data_size is not None:
            out += encode_uint(3, self.data_size)
        if self.requested_flags is not None:
            out += encode_uint(4, self.requested_flags)
        if self.transaction_id is not None:
            out += encode_uint(5, self.transaction_id)
        if self.urgency is not None:
            out += encode_uint(6, int(self.urgency))
        if self.modified_time is not None:
            out += encode_uint(7, self.modified_time)
        return bytes(out)

    @classmethod
    def parse(cls, data: bytes) -> "FileItemReference":
        fields = parse_fields(data)
        raw_uid = last_bytes(fields, 1)
        raw_type = last_bytes(fields, 2)
        return cls(
            parse_uuid(raw_uid) if raw_uid is not None else None,
            FileDataType.parse(raw_type) if raw_type is not None else None,
            last_varint(fields, 3),
            last_varint(fields, 4),
            last_varint(fields, 5),
            _enum(ItemUrgency, last_varint(fields, 6)),
            last_varint(fields, 7),
            fields,
        )


@dataclass(frozen=True, slots=True)
class ItemListRequest:
    session_id: int | None = None
    transaction_id: int | None = None
    max_item_count: int | None = None
    exclude_all_flags: tuple[UUID, ...] = ()
    get_flags: tuple[UUID, ...] = ()
    included_data_types: tuple[FileDataType, ...] = ()
    requested_modified_time: bool = False

    def encode(self) -> bytes:
        out = bytearray()
        if self.session_id is not None:
            out += encode_uint(1, self.session_id)
        if self.transaction_id is not None:
            out += encode_uint(2, self.transaction_id)
        if self.max_item_count is not None:
            out += encode_uint(3, self.max_item_count)
        for flag in self.exclude_all_flags:
            out += encode_message(4, encode_uuid(flag))
        for flag in self.get_flags:
            out += encode_message(5, encode_uuid(flag))
        for data_type in self.included_data_types:
            out += encode_message(6, data_type.encode())
        if self.requested_modified_time:
            out += encode_uint(7, 0)
        return bytes(out)


@dataclass(frozen=True, slots=True)
class ItemListResponse:
    status: ItemListStatus | int | None
    session_id: int | None
    next_transaction_id: int | None
    items: tuple[FileItemReference, ...]
    error_code: int | None = None
    abandoned_session_id: int | None = None
    malformed_data_type: FileDataType | None = None
    raw_fields: tuple[WireField, ...] = ()

    @classmethod
    def parse(cls, data: bytes) -> "ItemListResponse":
        fields = parse_fields(data)
        items: list[FileItemReference] = []
        for field in fields:
            if field.number == 4:
                if field.wire_type is not WireType.LENGTH_DELIMITED or not isinstance(field.value, bytes):
                    raise ProtobufWireError("ItemListResponse items field is not a message")
                items.append(FileItemReference.parse(field.value))
        malformed = last_bytes(fields, 7)
        return cls(
            _enum(ItemListStatus, last_varint(fields, 1)),
            last_varint(fields, 2),
            last_varint(fields, 3),
            tuple(items),
            last_varint(fields, 5),
            last_varint(fields, 6),
            FileDataType.parse(malformed) if malformed is not None else None,
            fields,
        )


@dataclass(frozen=True, slots=True)
class PullItemRequest:
    item: FileItemReference
    priority: int
    supported_transports: tuple[TransportProtocol | int, ...] = (TransportProtocol.MULTILINK_TRANSPORT_PIPE,)
    offset: int | None = None
    requested_compression_window: int | None = None

    def encode(self) -> bytes:
        out = bytearray(encode_message(1, self.item.encode()) + encode_uint(2, self.priority))
        for transport in self.supported_transports:
            out += encode_uint(3, int(transport))
        if self.offset is not None:
            out += encode_uint(4, self.offset)
        if self.requested_compression_window is not None:
            out += encode_uint(5, self.requested_compression_window)
        return bytes(out)


@dataclass(frozen=True, slots=True)
class PullItemResponse:
    result: ItemAccessResult | int | None
    transport: TransportProtocol | int | None
    transfer_handle: int | None
    file_path: str | None
    error_code: int | None
    error_source: int | None
    compression_window: int | None
    data_specific_error_source: int | None
    data_specific_error_code: int | None
    raw_fields: tuple[WireField, ...]

    @classmethod
    def parse(cls, data: bytes) -> "PullItemResponse":
        fields = parse_fields(data)
        raw_error = last_varint(fields, 5)
        raw_specific = last_varint(fields, 9)
        return cls(
            _enum(ItemAccessResult, last_varint(fields, 1)),
            _enum(TransportProtocol, last_varint(fields, 2)),
            last_varint(fields, 3),
            _stringsafe(last_bytes(fields, 4)),
            None if raw_error is None else decode_sint32(raw_error),
            last_varint(fields, 6),
            last_varint(fields, 7),
            last_varint(fields, 8),
            None if raw_specific is None else decode_sint32(raw_specific),
            fields,
        )


@dataclass(frozen=True, slots=True)
class TransferStatusRequest:
    uid: UUID | None = None
    transfer_handle: int | None = None
    failure_reason: TransferFailureReason | int | None = None
    transport_status: int | None = None
    transport_provider_id: int | None = None
    transport_error_code: int | None = None
    new_priority: int | None = None
    raw_fields: tuple[WireField, ...] = ()

    def encode(self) -> bytes:
        out = bytearray()
        if self.uid is not None:
            out += encode_message(1, encode_uuid(self.uid))
        if self.transfer_handle is not None:
            out += encode_uint(2, self.transfer_handle)
        if self.failure_reason is not None:
            out += encode_uint(3, int(self.failure_reason))
        if self.transport_status is not None:
            out += encode_uint(4, self.transport_status)
        if self.transport_provider_id is not None:
            out += encode_uint(5, self.transport_provider_id)
        if self.transport_error_code is not None:
            out += encode_uint(6, self.transport_error_code)
        if self.new_priority is not None:
            out += encode_uint(7, self.new_priority)
        return bytes(out)

    @classmethod
    def parse(cls, data: bytes) -> "TransferStatusRequest":
        fields = parse_fields(data)
        raw_uid = last_bytes(fields, 1)
        return cls(
            parse_uuid(raw_uid) if raw_uid is not None else None,
            last_varint(fields, 2),
            _enum(TransferFailureReason, last_varint(fields, 3)),
            last_varint(fields, 4),
            last_varint(fields, 5),
            last_varint(fields, 6),
            last_varint(fields, 7),
            fields,
        )


@dataclass(frozen=True, slots=True)
class TransferStatusResponse:
    next_transfer_priority: int | None = None

    def encode(self) -> bytes:
        if self.next_transfer_priority is None:
            return b""
        return encode_uint(1, self.next_transfer_priority)


@dataclass(frozen=True, slots=True)
class GetItemChecksumResponse:
    uid: UUID | None
    result: int | None
    checksum_truncated_md5: int | None
    raw_fields: tuple[WireField, ...]

    @classmethod
    def parse(cls, data: bytes) -> "GetItemChecksumResponse":
        fields = parse_fields(data)
        uid = last_bytes(fields, 1)
        return cls(
            parse_uuid(uid) if uid is not None else None,
            last_varint(fields, 2),
            last_fixed64(fields, 3),
            fields,
        )


def build_service_message(field_number: int, payload: bytes) -> bytes:
    return encode_message(field_number, payload)


def build_file_access_smart(service_payload: bytes) -> bytes:
    return build_smart_extension(SMART_FILE_ACCESS_EXTENSION, service_payload)


def parse_file_access_service(smart_bytes: bytes) -> tuple[WireField, ...]:
    smart = SmartMessage.parse(smart_bytes)
    service = smart.extension(SMART_FILE_ACCESS_EXTENSION)
    if service is None:
        raise ProtobufWireError("Smart message has no FileAccess extension")
    return parse_fields(service)


def service_message(smart_bytes: bytes, field_number: int) -> bytes | None:
    return last_bytes(parse_file_access_service(smart_bytes), field_number)


def build_item_list_smart(request: ItemListRequest) -> bytes:
    return build_file_access_smart(build_service_message(SERVICE_ITEM_LIST_REQUEST, request.encode()))


def parse_item_list_smart_response(smart_bytes: bytes) -> ItemListResponse:
    raw = service_message(smart_bytes, SERVICE_ITEM_LIST_RESPONSE)
    if raw is None:
        raise ProtobufWireError("FileAccess service has no item-list response")
    return ItemListResponse.parse(raw)


def build_pull_item_smart(request: PullItemRequest) -> bytes:
    return build_file_access_smart(build_service_message(SERVICE_PULL_ITEM_REQUEST, request.encode()))


def parse_pull_item_smart_response(smart_bytes: bytes) -> PullItemResponse:
    raw = service_message(smart_bytes, SERVICE_PULL_ITEM_RESPONSE)
    if raw is None:
        raise ProtobufWireError("FileAccess service has no pull-item response")
    return PullItemResponse.parse(raw)


def parse_transfer_status_smart_request(smart_bytes: bytes) -> TransferStatusRequest | None:
    raw = service_message(smart_bytes, SERVICE_TRANSFER_STATUS_REQUEST)
    return TransferStatusRequest.parse(raw) if raw is not None else None


def build_transfer_status_smart_response(response: TransferStatusResponse | None = None) -> bytes:
    payload = (response or TransferStatusResponse()).encode()
    return build_file_access_smart(build_service_message(SERVICE_TRANSFER_STATUS_RESPONSE, payload))


@dataclass(frozen=True, slots=True)
class MlrPipeConfigure:
    """Ten-byte MultiLink Transport Pipe configure command recovered statically."""

    transfer_handle: int
    direction: MlrPipeDirection | int

    def encode(self) -> bytes:
        if not 0 <= self.transfer_handle <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError("transfer_handle must fit uint64")
        direction = int(self.direction)
        if direction not in (0, 1):
            raise ValueError("MLR pipe direction must be 0 (read) or 1 (write)")
        return bytes([0, direction]) + self.transfer_handle.to_bytes(8, "little")


@dataclass(frozen=True, slots=True)
class MlrPipeConfigureResponse:
    general_status: int
    configure_status: int | None

    @property
    def successful(self) -> bool:
        return self.general_status == 0 and self.configure_status == 0

    @classmethod
    def parse(cls, data: bytes) -> "MlrPipeConfigureResponse":
        if len(data) < 2:
            raise ValueError("MLR configure response missing general status")
        general = data[1]
        configure = data[2] if general == 0 and len(data) >= 3 else None
        if general == 0 and configure is None:
            raise ValueError("MLR configure response missing configure status")
        return cls(general, configure)
