"""Set Device Settings (5026) and System Event (5030) time-update codecs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .time_sync import garmin_seconds_from_unix, next_dst_transitions

SETTINGS_TIME = 1
SETTINGS_DST_SAVINGS = 2
SETTINGS_UTC_OFFSET = 3
SETTINGS_NEXT_DST_START = 4
SETTINGS_NEXT_DST_END = 5
TIME_UPDATED_SYSTEM_EVENT = 16
TIME_REQUEST_CONFIGURATION_FLAG = 71


@dataclass(frozen=True, slots=True)
class SettingEntry:
    setting_id: int
    value: bytes


class DeviceSettingsError(ValueError):
    pass


def encode_entries(entries: list[SettingEntry]) -> bytes:
    if len(entries) > 0xFF:
        raise DeviceSettingsError("too many setting entries")
    out = bytearray([len(entries)])
    for entry in entries:
        if not 0 <= entry.setting_id <= 0xFF:
            raise DeviceSettingsError("setting id out of byte range")
        if len(entry.value) > 0xFF:
            raise DeviceSettingsError("setting value exceeds one-byte length")
        out += bytes([entry.setting_id, len(entry.value)]) + entry.value
    return bytes(out)


def parse_entries(payload: bytes) -> list[SettingEntry]:
    if not payload:
        raise DeviceSettingsError("5026 payload is empty")
    count = payload[0]
    pos = 1
    out: list[SettingEntry] = []
    for _ in range(count):
        if pos + 2 > len(payload):
            raise DeviceSettingsError("truncated setting header")
        setting_id, size = payload[pos], payload[pos + 1]
        pos += 2
        end = pos + size
        if end > len(payload):
            raise DeviceSettingsError("truncated setting value")
        out.append(SettingEntry(setting_id, bytes(payload[pos:end])))
        pos = end
    if pos != len(payload):
        raise DeviceSettingsError("trailing bytes after setting entries")
    return out


def build_legacy_time_settings(now: datetime) -> bytes:
    """Build the five-entry legacy 5026 time/timezone payload.

    Values mirror the recovered CurrentTimeManager: Garmin-epoch current time,
    current DST savings (not raw offset), total UTC offset, next DST start, and
    next DST end. Each is a four-byte little-endian value.
    """
    if now.tzinfo is None:
        raise DeviceSettingsError("now must be timezone-aware")
    unix = int(now.timestamp())
    dst = int((now.dst() or timedelta(0)).total_seconds())
    offset = int((now.utcoffset() or timedelta(0)).total_seconds())
    start, end = next_dst_transitions(now)
    values = [
        (SETTINGS_TIME, garmin_seconds_from_unix(unix)),
        (SETTINGS_DST_SAVINGS, dst),
        (SETTINGS_UTC_OFFSET, offset),
        (SETTINGS_NEXT_DST_START, start),
        (SETTINGS_NEXT_DST_END, end),
    ]
    return encode_entries(
        [SettingEntry(setting_id, (value & 0xFFFFFFFF).to_bytes(4, "little")) for setting_id, value in values]
    )


def build_time_updated_event() -> bytes:
    """Build System Event 5030 data for TIME_UPDATED.

    Static code sends the event ID followed by a zero byte, then waits up to
    five seconds for a device using configuration flag 71 to request 5052.
    """
    return bytes([TIME_UPDATED_SYSTEM_EVENT, 0])
