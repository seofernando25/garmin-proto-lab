from __future__ import annotations

from datetime import datetime, timezone

from hypothesis import given, strategies as st

from garmin_proto_lab.battery import BatteryState, build_phone_battery_status, parse_battery_status
from garmin_proto_lab.ble_stream import NotificationByteStream, split_att_writes
from garmin_proto_lab.device_info import parse_device_information
from garmin_proto_lab.time_sync import GARMIN_EPOCH_UNIX_SECONDS, build_basic_response, garmin_seconds_from_unix


@given(st.binary(max_size=4096), st.integers(min_value=1, max_value=512))
def test_ble_write_splitting_reassembles(data: bytes, limit: int) -> None:
    chunks = split_att_writes(data, limit)
    assert b"".join(chunks) == data
    assert all(0 < len(chunk) <= limit for chunk in chunks) or not data


def test_notification_stream_concatenates_packets() -> None:
    stream = NotificationByteStream()
    stream.feed(b"abc")
    stream.feed(b"defg")
    assert stream.read(5) == b"abcde"
    assert stream.read() == b"fg"


def test_device_information_minimal_payload() -> None:
    payload = bytearray()
    payload += (150).to_bytes(2, "little")
    payload += (1234).to_bytes(2, "little")
    payload += (0x12345678).to_bytes(4, "little")
    payload += (901).to_bytes(2, "little")
    payload += (509).to_bytes(2, "little")
    for text in (b"Venu", b"My Watch", b"Model X"):
        payload.append(len(text))
        payload += text
    payload += bytes([1])
    payload += bytes.fromhex("665544332211")  # displayed 11:22:33:44:55:66
    payload += bytes.fromhex("ccbbaa998877")  # displayed 77:88:99:AA:BB:CC
    payload += bytes([3])

    info = parse_device_information(bytes(payload))
    assert info.protocol_version == 150
    assert info.product_number == 1234
    assert info.unit_id == 0x12345678
    assert info.software_version == 901
    assert info.max_packet_size == 509
    assert info.bluetooth_friendly_name == "Venu"
    assert info.device_name == "My Watch"
    assert info.model_name == "Model X"
    assert info.dual_pairing is True
    assert info.ble_mac == "11:22:33:44:55:66"
    assert info.classic_mac == "77:88:99:AA:BB:CC"
    assert info.limitation == 3


def test_battery_payload() -> None:
    status = parse_battery_status(bytes([0x20, 0, 77, 0, 0, 0]))
    assert status.state is BatteryState.OK
    assert status.capacity_percent == 77
    assert build_phone_battery_status(42) == bytes([0xFF, 0, 42, 0xFF, 0xFF, 0xFF])


def test_garmin_epoch_and_basic_time_response() -> None:
    assert garmin_seconds_from_unix(GARMIN_EPOCH_UNIX_SECONDS) == 0
    now = datetime(2026, 9, 13, 16, 0, 0, tzinfo=timezone.utc)
    response = build_basic_response(bytes.fromhex("01020304"), now)
    raw = response.to_payload()
    assert raw[:4] == bytes.fromhex("01020304")
    assert int.from_bytes(raw[4:8], "little") == int(now.timestamp()) - GARMIN_EPOCH_UNIX_SECONDS
    assert int.from_bytes(raw[8:12], "little") == 0
    assert len(raw) == 20


def test_dst_transition_pair_for_toronto_2026() -> None:
    from zoneinfo import ZoneInfo
    from garmin_proto_lab.time_sync import next_dst_transitions

    zone = ZoneInfo("America/Toronto")
    now = datetime(2026, 1, 15, 12, 0, tzinfo=zone)
    start, end = next_dst_transitions(now)
    expected_start = int(datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc).timestamp()) - GARMIN_EPOCH_UNIX_SECONDS
    expected_end = int(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc).timestamp()) - GARMIN_EPOCH_UNIX_SECONDS
    assert start == expected_start
    assert end == expected_end
