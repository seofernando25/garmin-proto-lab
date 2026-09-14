from __future__ import annotations

import struct

import pytest

from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.fit import extract_wellness_samples, fit_semantic_summary


def _definition(local: int, global_message: int, fields: list[tuple[int, int, int]]) -> bytes:
    out = bytearray([0x40 | local, 0, 0])
    out += global_message.to_bytes(2, "little")
    out.append(len(fields))
    for number, size, base_type in fields:
        out += bytes((number, size, base_type))
    return bytes(out)


def _fit(records: bytes) -> bytes:
    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    header = header12 + crc16_arc(header12).to_bytes(2, "little")
    body = header + records
    return body + crc16_arc(body).to_bytes(2, "little")


def _extended_wellness_file() -> bytes:
    records = bytearray()

    records += _definition(0, 140, [(253, 4, 0x86), (4, 1, 0x02), (7, 4, 0x85), (9, 2, 0x84), (14, 2, 0x84), (20, 1, 0x02)])
    records += bytes([0]) + struct.pack("<IBiHHB", 1000, 35, 3 * 65536, 720, 168, 21)

    records += _definition(1, 227, [(0, 2, 0x83), (1, 4, 0x86), (3, 1, 0x01)])
    records += bytes([1]) + struct.pack("<hIb", 42, 1001, 67)

    records += _definition(2, 273, [(253, 4, 0x86), (1, 2, 0x84), (2, 4, 0x86), (4, 5, 0x07)])
    records += bytes([2]) + struct.pack("<IHI", 1002, 60, 50000) + b"ETE\x00\x00"

    records += _definition(3, 274, [(0, 4, 0x0D)])
    records += bytes([3]) + b"\x01\x02\x03\x04"

    records += _definition(4, 346, [(6, 1, 0x02), (11, 1, 0x02), (14, 1, 0x02), (15, 2, 0x84)])
    records += bytes([4]) + struct.pack("<BBBH", 82, 3, 74, 1250)

    records += _definition(5, 370, [(253, 4, 0x86), (0, 2, 0x84), (1, 2, 0x84), (2, 2, 0x84), (3, 2, 0x84), (4, 2, 0x84), (5, 2, 0x84), (6, 1, 0x00)])
    records += bytes([5]) + struct.pack("<IHHHHHHB", 1003, 50 * 128, 48 * 128, 72 * 128, 45 * 128, 42 * 128, 58 * 128, 4)

    records += _definition(6, 371, [(253, 4, 0x86), (0, 2, 0x84)])
    records += bytes([6]) + struct.pack("<IH", 1004, 52 * 128)

    records += _definition(7, 397, [(253, 4, 0x86), (1, 4, 0x88)])
    records += bytes([7]) + struct.pack("<If", 1005, -0.35)

    records += _definition(8, 398, [(253, 4, 0x86), (0, 4, 0x86), (1, 4, 0x88), (2, 4, 0x88), (3, 1, 0x02)])
    records += bytes([8]) + struct.pack("<IIffB", 1006, 50000, 0.42, 0.18, 1)

    return _fit(bytes(records))


def test_extended_wellness_messages_are_semantic_and_json_ready() -> None:
    data = _extended_wellness_file()
    samples = extract_wellness_samples(data)
    by_kind = {sample.kind: sample for sample in samples}

    metrics = by_kind["physiological_metrics"]
    assert metrics.value("aerobic_training_effect") == pytest.approx(3.5)
    assert metrics.value("met_max") == pytest.approx(3.0)
    assert metrics.value("recovery_time_min") == 720
    assert metrics.value("lactate_threshold_heart_rate_bpm") == 168
    assert metrics.value("anaerobic_training_effect") == pytest.approx(2.1)

    assert by_kind["stress"].value("stress_level") == 42
    assert by_kind["stress"].value("body_energy") == 67
    assert by_kind["sleep_data_info"].value("sample_length") == 60
    assert by_kind["sleep_data_info"].value("version") == "ETE"
    assert by_kind["sleep_data_raw"].value("raw_sample") == b"\x01\x02\x03\x04"

    assert by_kind["sleep_stats"].value("overall_sleep_score") == 82
    assert by_kind["sleep_stats"].value("awakenings_count") == 3
    assert by_kind["sleep_stats"].value("average_stress_during_sleep") == pytest.approx(12.5)
    hrv_summary = by_kind["hrv_summary"]
    assert hrv_summary.value("weekly_average_ms") == pytest.approx(50.0)
    assert hrv_summary.value("last_night_average_ms") == pytest.approx(48.0)
    assert hrv_summary.value("status") == "balanced"
    assert by_kind["hrv_value"].value("value_ms") == pytest.approx(52.0)
    assert by_kind["skin_temperature_raw"].value("deviation") == pytest.approx(-0.35)
    assert by_kind["skin_temperature_overnight"].value("average_deviation") == pytest.approx(0.42)
    summary = fit_semantic_summary(data, include_wellness_samples=True, include_records=True)
    assert summary["wellness_sample_count"] == 9
    assert summary["message_counts"]["HRV_SUMMARY"] == 1
    assert summary["message_counts"]["SKIN_TEMP_OVERNIGHT"] == 1


def _monitoring_timestamp_file() -> bytes:
    records = bytearray()
    records += _definition(0, 55, [(253, 4, 0x86)])
    records += bytes([0]) + struct.pack("<I", 65530)
    records += _definition(0, 55, [(26, 2, 0x84), (24, 1, 0x0D), (27, 1, 0x02)])
    records += bytes([0]) + struct.pack("<H", 5) + bytes([(3 << 5) | 6, 73])
    return _fit(bytes(records))


def test_monitoring_timestamp16_and_packed_activity_fallback() -> None:
    samples = [sample for sample in extract_wellness_samples(_monitoring_timestamp_file()) if sample.kind == "monitoring"]
    assert len(samples) == 2
    first, second = samples
    assert first.timestamp_garmin == 65530
    assert second.timestamp_garmin == 65541
    assert second.value("activity_type") == "walking"
    assert second.value("packed_intensity") == 3
    assert second.value("heart_rate_bpm") == 73


def _hsa_file() -> bytes:
    records = bytearray()
    records += _definition(0, 302, [(253, 4, 0x86), (0, 2, 0x84), (1, 2, 0x84), (2, 4, 0x83), (3, 4, 0x83), (4, 4, 0x83), (5, 4, 0x86)])
    records += bytes([0]) + struct.pack("<IHHhhhhhhI", 2000, 125, 20, 1024, -1024, 512, 0, 2048, -512, 123456)

    records += _definition(1, 304, [(253, 4, 0x86), (0, 2, 0x84), (1, 8, 0x86)])
    records += bytes([1]) + struct.pack("<IHII", 2001, 60, 100, 125)

    records += _definition(2, 305, [(253, 4, 0x86), (0, 2, 0x84), (1, 2, 0x02), (2, 2, 0x02)])
    records += bytes([2]) + struct.pack("<IHBBBB", 2002, 30, 96, 97, 80, 82)

    records += _definition(3, 306, [(253, 4, 0x86), (0, 2, 0x84), (1, 3, 0x01)])
    records += bytes([3]) + struct.pack("<IHbbb", 2003, 180, 20, 45, -1)

    records += _definition(4, 307, [(253, 4, 0x86), (0, 2, 0x84), (1, 4, 0x83)])
    records += bytes([4]) + struct.pack("<IHhh", 2004, 60, 1450, 1600)

    records += _definition(5, 308, [(253, 4, 0x86), (0, 2, 0x84), (1, 1, 0x02), (2, 3, 0x02)])
    records += bytes([5]) + struct.pack("<IHBBBB", 2005, 15, 1, 62, 65, 64)
    return _fit(bytes(records))


def test_hsa_profile_messages_are_projected() -> None:
    samples = extract_wellness_samples(_hsa_file())
    by_kind = {sample.kind: sample for sample in samples}
    accel = by_kind["hsa_accelerometer"]
    assert accel.value("timestamp_ms") == 125
    assert accel.value("sampling_interval_ms") == 20
    assert accel.value("accel_x_mg") == pytest.approx((1000.0, -1000.0))
    assert accel.value("accel_y_mg") == pytest.approx((500.0, 0.0))
    assert by_kind["hsa_steps"].value("steps") == (100, 125)
    assert by_kind["hsa_spo2"].value("spo2_percent") == (96, 97)
    assert by_kind["hsa_spo2"].value("confidence") == (80, 82)
    assert by_kind["hsa_stress"].value("stress_levels") == (20, 45, -1)
    assert by_kind["hsa_respiration"].value("respiration_rate_breaths_per_min") == pytest.approx((14.5, 16.0))
    assert by_kind["hsa_heart_rate"].value("status") == 1
    assert by_kind["hsa_heart_rate"].value("heart_rate_bpm") == (62, 65, 64)
