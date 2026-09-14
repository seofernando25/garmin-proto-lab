"""Stable message-ID census extracted as protocol facts."""
MESSAGE_NAMES = {
    5000: "acknowledgement", 5002: "download_file", 5003: "upload_file",
    5004: "file_data", 5005: "create_file", 5006: "delete_file",
    5007: "directory_filter", 5008: "set_file_flags", 5009: "file_ready",
    5011: "fit_definition", 5012: "fit_data", 5014: "weather_request",
    5015: "weather_alert", 5016: "live_tracking_request",
    5019: "ephemeris_data_request", 5020: "ephemeris_data",
    5022: "cancel_file_transfer", 5023: "battery_status", 5024: "device_information",
    5025: "live_tracking_stop", 5026: "set_device_settings", 5027: "queued_download",
    5028: "ephemeris_epo_request", 5029: "ephemeris_epo_data", 5030: "system_event",
    5031: "supported_file_types", 5033: "gncs_notification_source",
    5034: "gncs_control_point", 5035: "gncs_data_source",
    5036: "gncs_notification_service_subscription", 5037: "sync_request",
    5039: "find_my_phone", 5040: "cancel_find_my_phone", 5041: "music_control",
    5042: "music_control_capabilities", 5043: "protobuf_request", 5044: "protobuf_response",
    5045: "cancel_protobuf", 5046: "live_tracking_auto_start", 5047: "live_tracking_auto_cancel",
    5048: "live_tracking_auto_failure", 5049: "music_entity_update", 5050: "configuration",
    5052: "current_time", 5054: "compressed_file_data", 5101: "auth_negotiation_begin",
    5102: "ltk_reconnect", 5103: "stk_begin_generation", 5104: "confirm_number",
    5105: "stk_rand_number", 5106: "stk_generation_status", 5107: "ltk_key_distribution",
    5108: "session_key_skd_distribution", 5109: "session_key_verification",
    5110: "passkey_redisplay", 5111: "secure_session", 5112: "out_of_band_passkey_data",
}

from dataclasses import dataclass
from enum import Enum


class MessageDisposition(str, Enum):
    IMPLEMENTED = "implemented"
    OUTSIDE_FITNESS_SCOPE = "outside_fitness_scope"
    STATIC_NO_CALLSITE = "static_no_callsite"


@dataclass(frozen=True, slots=True)
class MessageFamily:
    message_id: int
    name: str
    disposition: MessageDisposition
    rationale: str


_IMPLEMENTED = frozenset({
    5000, 5002, 5004, 5007, 5008, 5009,
    5022, 5023, 5024, 5026, 5027, 5030, 5031,
    5033, 5034, 5035, 5036, 5037,
    5043, 5044, 5045, 5050, 5052, 5054,
    5101, 5102, 5103, 5104, 5105, 5106, 5107, 5108, 5109, 5111, 5112,
})
_STATIC_NO_CALLSITE = frozenset({5110})
_OUTSIDE_FITNESS_SCOPE = frozenset(MESSAGE_NAMES) - _IMPLEMENTED - _STATIC_NO_CALLSITE

MESSAGE_FAMILIES = {
    message_id: MessageFamily(
        message_id,
        name,
        (
            MessageDisposition.IMPLEMENTED
            if message_id in _IMPLEMENTED
            else MessageDisposition.STATIC_NO_CALLSITE
            if message_id in _STATIC_NO_CALLSITE
            else MessageDisposition.OUTSIDE_FITNESS_SCOPE
        ),
        (
            "implemented by the pairing, notification, status, sync or fitness-transfer stack"
            if message_id in _IMPLEMENTED
            else "message name exists in the APK map but no sender/receiver call site exists in this build"
            if message_id in _STATIC_NO_CALLSITE
            else "watch protocol family not required for direct pairing and fitness-data extraction"
        ),
    )
    for message_id, name in MESSAGE_NAMES.items()
}

if set(MESSAGE_FAMILIES) != set(MESSAGE_NAMES):
    raise RuntimeError("message census does not cover every recovered message ID")
