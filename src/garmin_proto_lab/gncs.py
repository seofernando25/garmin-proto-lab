"""Garmin Notification Communication Service (GNCS) transport codecs.

These helpers implement only transport facts established in P-0500..P-0502.
They do not attempt to clone Garmin's Android notification UI or application
policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .crc import crc16_arc
from .xxtea import decrypt_padded, encrypt_padded

GNCS_NOTIFICATION_SOURCE_MESSAGE_ID = 5033
GNCS_CONTROL_POINT_MESSAGE_ID = 5034
GNCS_DATA_SOURCE_MESSAGE_ID = 5035
GNCS_SUBSCRIPTION_MESSAGE_ID = 5036
MAX_ANCS_PAYLOAD = 8192
PHONE_NUMBER_FEATURE = 0x01


class GncsError(ValueError):
    pass


class ControlPointResponseType(IntEnum):
    SUCCESSFUL = 0
    ANCS_ERROR_OCCURRED = 1
    INVALID_PARAMETERS = 2


class AncsSemanticError(IntEnum):
    NO_ERROR = 0
    UNKNOWN_COMMAND = 160
    INVALID_COMMAND = 161
    INVALID_PARAMETER = 162


@dataclass(frozen=True, slots=True)
class ControlPointResponse:
    response_type: ControlPointResponseType | int
    ancs_error: AncsSemanticError | int

    def encode(self) -> bytes:
        return bytes([int(self.response_type) & 0xFF, int(self.ancs_error) & 0xFF])

    @classmethod
    def parse(cls, payload: bytes) -> "ControlPointResponse":
        if len(payload) != 2:
            raise GncsError("control-point response must be exactly two bytes")
        try:
            response: ControlPointResponseType | int = ControlPointResponseType(payload[0])
        except ValueError:
            response = payload[0]
        try:
            error: AncsSemanticError | int = AncsSemanticError(payload[1])
        except ValueError:
            error = payload[1]
        return cls(response, error)


def decode_control_point_payload(payload: bytes, session_key: bytes | None = None) -> bytes:
    """Remove optional GNCS padded-XXTEA protection from message 5034."""
    return decrypt_padded(payload, session_key) if session_key is not None else bytes(payload)


def encode_notification_source_payload(payload: bytes, session_key: bytes | None = None) -> bytes:
    """Prepare the nine-byte ANCS Notification Source body for GFDI 5033.

    Static evidence sends the raw body when no GNCS session key is present and
    uses self-describing padded XXTEA when a session key exists.
    """
    if len(payload) != 9:
        raise GncsError("notification-source semantic payload must be exactly nine bytes")
    return encrypt_padded(payload, session_key) if session_key is not None else bytes(payload)


class SubscriptionIntent(IntEnum):
    UNSUBSCRIBE = 0
    SUBSCRIBE = 1


class SubscriptionStatus(IntEnum):
    SUCCESSFUL = 0
    NOT_SUPPORTED = 1
    NOT_READY = 2


@dataclass(frozen=True, slots=True)
class SubscriptionRequest:
    intent: SubscriptionIntent | int
    feature_flags: int = 0

    def encode(self) -> bytes:
        intent = int(self.intent)
        if intent not in (0, 1):
            raise GncsError("subscription intent must be 0 or 1")
        if not 0 <= self.feature_flags <= 0xFF:
            raise GncsError("feature_flags out of byte range")
        return bytes([intent, self.feature_flags])

    @classmethod
    def parse(cls, payload: bytes) -> "SubscriptionRequest":
        if len(payload) != 2:
            raise GncsError("subscription request must be exactly two bytes")
        try:
            intent: SubscriptionIntent | int = SubscriptionIntent(payload[0])
        except ValueError:
            intent = payload[0]
        return cls(intent, payload[1])

    @property
    def requests_phone_number_feature(self) -> bool:
        return bool(self.feature_flags & PHONE_NUMBER_FEATURE)


@dataclass(frozen=True, slots=True)
class SubscriptionResponse:
    status: SubscriptionStatus | int
    intent: SubscriptionIntent | int
    feature_flags: int

    def encode(self) -> bytes:
        status = int(self.status)
        intent = int(self.intent)
        if not 0 <= status <= 0xFF or not 0 <= intent <= 0xFF or not 0 <= self.feature_flags <= 0xFF:
            raise GncsError("subscription response field out of byte range")
        return bytes([status, intent, self.feature_flags])

    @classmethod
    def parse(cls, payload: bytes) -> "SubscriptionResponse":
        if len(payload) != 3:
            raise GncsError("subscription response must be exactly three bytes")
        try:
            status: SubscriptionStatus | int = SubscriptionStatus(payload[0])
        except ValueError:
            status = payload[0]
        try:
            intent: SubscriptionIntent | int = SubscriptionIntent(payload[1])
        except ValueError:
            intent = payload[1]
        return cls(status, intent, payload[2])


class DataSourceStatus(IntEnum):
    TRANSFER_SUCCESSFUL = 0
    RESEND_LAST_DATA_PACKET = 1
    ABORT_REQUEST = 2
    CRC_MISMATCH = 3
    DATA_OFFSET_MISMATCH = 4


@dataclass(frozen=True, slots=True)
class DataSourceResponse:
    status: DataSourceStatus | int

    @classmethod
    def parse(cls, payload: bytes) -> "DataSourceResponse":
        if len(payload) != 1:
            raise GncsError("data-source response must be exactly one byte")
        try:
            status: DataSourceStatus | int = DataSourceStatus(payload[0])
        except ValueError:
            status = payload[0]
        return cls(status)

    def encode(self) -> bytes:
        value = int(self.status)
        if not 0 <= value <= 0xFF:
            raise GncsError("data-source status out of byte range")
        return bytes([value])


@dataclass(frozen=True, slots=True)
class DataSourceChunk:
    total_payload_size: int
    transferred_crc: int
    data_offset: int
    transmitted_data: bytes

    def encode(self) -> bytes:
        if not 0 <= self.total_payload_size <= 0xFFFF:
            raise GncsError("total payload size out of uint16 range")
        if not 0 <= self.transferred_crc <= 0xFFFF:
            raise GncsError("CRC out of uint16 range")
        if not 0 <= self.data_offset <= 0xFFFF:
            raise GncsError("data offset out of uint16 range")
        return (
            self.total_payload_size.to_bytes(2, "little")
            + self.transferred_crc.to_bytes(2, "little")
            + self.data_offset.to_bytes(2, "little")
            + self.transmitted_data
        )

    @classmethod
    def parse(cls, payload: bytes) -> "DataSourceChunk":
        if len(payload) < 6:
            raise GncsError("data-source payload shorter than six bytes")
        return cls(
            total_payload_size=int.from_bytes(payload[0:2], "little"),
            transferred_crc=int.from_bytes(payload[2:4], "little"),
            data_offset=int.from_bytes(payload[4:6], "little"),
            transmitted_data=bytes(payload[6:]),
        )


@dataclass(frozen=True, slots=True)
class BuiltDataSourceChunk:
    payload: bytes
    source_bytes_consumed: int
    next_crc_seed: int
    next_data_offset: int


def build_data_source_chunk(
    ancs_payload: bytes,
    *,
    data_offset: int,
    crc_seed: int,
    gfdi_payload_limit: int,
    session_key: bytes | None = None,
) -> BuiltDataSourceChunk:
    """Build one 5035 chunk with the same static sizing/CRC semantics.

    ``data_offset`` and the total ANCS length are 16-bit protocol fields.  When
    a session key is provided, the selected source bytes are transformed by
    the GNCS padded-XXTEA mode before their transmitted-byte CRC is updated.

    The static implementation reserves ten bytes from the GFDI payload limit:
    six bytes for this header and four for worst-case padded-encryption growth.
    This conservative limit is also used for unencrypted chunks so packetization
    is stable if encryption becomes active.
    """
    if not ancs_payload or len(ancs_payload) > MAX_ANCS_PAYLOAD:
        raise GncsError("ANCS payload must contain 1..8192 bytes")
    if not 0 <= data_offset < len(ancs_payload):
        raise GncsError("data_offset must identify a byte in the ANCS payload")
    if not 0 <= data_offset <= 0xFFFF:
        raise GncsError("data_offset out of uint16 range")
    if not 0 <= crc_seed <= 0xFFFF:
        raise GncsError("crc_seed out of uint16 range")
    max_source = gfdi_payload_limit - 10
    if max_source <= 0:
        raise GncsError("GFDI payload limit is too small for GNCS")
    source = ancs_payload[data_offset : data_offset + max_source]
    transmitted = encrypt_padded(source, session_key) if session_key is not None else source
    crc = crc16_arc(transmitted, crc_seed)
    chunk = DataSourceChunk(len(ancs_payload), crc, data_offset, transmitted)
    return BuiltDataSourceChunk(
        payload=chunk.encode(),
        source_bytes_consumed=len(source),
        next_crc_seed=crc,
        next_data_offset=data_offset + len(source),
    )
