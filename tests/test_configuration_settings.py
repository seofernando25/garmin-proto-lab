from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.configuration import (
    ConfigurationError,
    bitset_from_flags,
    encode_configuration,
    flags_from_bitset,
    parse_configuration,
)
from garmin_proto_lab.device_settings import (
    SETTINGS_DST_SAVINGS,
    SETTINGS_NEXT_DST_END,
    SETTINGS_NEXT_DST_START,
    SETTINGS_TIME,
    SETTINGS_UTC_OFFSET,
    TIME_REQUEST_CONFIGURATION_FLAG,
    build_legacy_time_settings,
    build_time_updated_event,
    parse_entries,
)
from garmin_proto_lab.time_sync import GARMIN_EPOCH_UNIX_SECONDS


@given(st.sets(st.integers(min_value=0, max_value=255), max_size=64))
def test_configuration_bitset_roundtrip(flags: set[int]) -> None:
    raw = bitset_from_flags(flags)
    assert flags_from_bitset(raw) == frozenset(flags)
    payload = encode_configuration(flags)
    assert parse_configuration(payload, allow_trailing=False).flags == frozenset(flags)


def test_configuration_legacy_bit3_implies_effective_bit4() -> None:
    cfg = parse_configuration(encode_configuration({3, 71}))
    assert cfg.flags == frozenset({3, 71})
    assert cfg.effective_flags() == frozenset({3, 4, 71})
    assert 4 not in parse_configuration(encode_configuration({3, 90})).effective_flags()


def test_configuration_length_and_trailing_validation() -> None:
    with pytest.raises(ConfigurationError):
        parse_configuration(b"")
    with pytest.raises(ConfigurationError):
        parse_configuration(b"\x02\x01")
    cfg = parse_configuration(b"\x01\x08extra")
    assert cfg.flags == frozenset({3})
    assert cfg.trailing == b"extra"
    with pytest.raises(ConfigurationError):
        parse_configuration(b"\x01\x08extra", allow_trailing=False)


def test_legacy_time_settings_exact_tlv_shape_utc() -> None:
    now = datetime(2026, 9, 13, 16, 0, tzinfo=timezone.utc)
    raw = build_legacy_time_settings(now)
    assert len(raw) == 31
    entries = parse_entries(raw)
    assert [e.setting_id for e in entries] == [1, 2, 3, 4, 5]
    assert all(len(e.value) == 4 for e in entries)
    values = {e.setting_id: int.from_bytes(e.value, "little") for e in entries}
    assert values[SETTINGS_TIME] == int(now.timestamp()) - GARMIN_EPOCH_UNIX_SECONDS
    assert values[SETTINGS_DST_SAVINGS] == 0
    assert values[SETTINGS_UTC_OFFSET] == 0
    assert values[SETTINGS_NEXT_DST_START] == 0
    assert values[SETTINGS_NEXT_DST_END] == 0


def test_legacy_time_settings_toronto_dst_and_event_path() -> None:
    zone = ZoneInfo("America/Toronto")
    now = datetime(2026, 7, 1, 12, 0, tzinfo=zone)
    entries = parse_entries(build_legacy_time_settings(now))
    values = {e.setting_id: int.from_bytes(e.value, "little", signed=False) for e in entries}
    # During July Toronto is UTC-4 with one hour of DST. Signed -14400 is sent
    # as its unsigned 32-bit little-endian representation.
    assert values[SETTINGS_DST_SAVINGS] == 3600
    assert values[SETTINGS_UTC_OFFSET] == ((-4 * 3600) & 0xFFFFFFFF)
    assert values[SETTINGS_NEXT_DST_START] != 0
    assert values[SETTINGS_NEXT_DST_END] != 0
    assert build_time_updated_event() == bytes([16, 0])
    assert TIME_REQUEST_CONFIGURATION_FLAG == 71
