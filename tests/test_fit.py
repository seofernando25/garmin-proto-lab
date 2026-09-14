from __future__ import annotations

import pytest

from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.fit import (
    fit_file_type_from_data_type_name,
    FitError,
    FitFileType,
    classify_directory_fit_subtype,
    inspect_fit,
    is_activity_or_health_data_type_name,
    is_activity_or_health_subtype,
    parse_fit_header,
)


def _fit_file(file_type: int = 4) -> bytes:
    # local message 0 definition for global message 0 (file_id):
    # field 0 type u8 enum, field 1 manufacturer uint16.
    records = bytes(
        [
            0x40,  # definition, local message 0
            0x00,  # reserved
            0x00,  # little-endian architecture
            0x00,
            0x00,  # global message 0
            0x02,  # two fields
            0x00,
            0x01,
            0x00,  # file type enum/u8
            0x01,
            0x02,
            0x84,  # manufacturer uint16
            0x00,  # data record local 0
            file_type,
            0x01,
            0x00,
        ]
    )
    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    header_crc = crc16_arc(header12)
    body = header12 + header_crc.to_bytes(2, "little") + records
    return body + crc16_arc(body).to_bytes(2, "little")


def test_fit_header_and_activity_file_id_inspection() -> None:
    data = _fit_file(FitFileType.ACTIVITY)
    header = parse_fit_header(data)
    assert header.header_size == 14
    assert header.data_size == 16
    inspection = inspect_fit(data)
    assert inspection.file_type is FitFileType.ACTIVITY
    assert inspection.is_activity_or_health is True


def test_directory_subtype_classification_for_activity_health() -> None:
    assert classify_directory_fit_subtype(128, 4) is FitFileType.ACTIVITY
    assert classify_directory_fit_subtype(128, 15) is FitFileType.MONITORING_A
    assert classify_directory_fit_subtype(128, 32) is FitFileType.MONITORING_B
    assert classify_directory_fit_subtype(128, 49) is FitFileType.SLEEP_DATA
    assert is_activity_or_health_subtype(128, 4)
    assert is_activity_or_health_subtype(128, 49)
    assert not is_activity_or_health_subtype(128, 5)
    assert classify_directory_fit_subtype(5, 4) is None
    assert classify_directory_fit_subtype(128, 222) == 222


def test_fit_integrity_and_truncation_fail_closed() -> None:
    data = bytearray(_fit_file())
    data[12] ^= 1
    with pytest.raises(FitError, match="header CRC"):
        parse_fit_header(bytes(data))

    with pytest.raises(FitError, match="signature"):
        parse_fit_header(bytes([12, 0, 0, 0, 0, 0, 0, 0]) + b"NOPE")

    data = _fit_file()
    with pytest.raises(FitError, match="truncated"):
        inspect_fit(data[:-5])

    bad_file_crc = bytearray(_fit_file())
    bad_file_crc[-1] ^= 0x01
    with pytest.raises(FitError, match="file CRC"):
        inspect_fit(bytes(bad_file_crc))


def test_next_gen_fit_data_type_name_classification() -> None:
    assert fit_file_type_from_data_type_name("FIT_TYPE_4") is FitFileType.ACTIVITY
    assert fit_file_type_from_data_type_name("FIT_TYPE_32") is FitFileType.MONITORING_B
    assert fit_file_type_from_data_type_name("FIT_TYPE_49") is FitFileType.SLEEP_DATA
    assert fit_file_type_from_data_type_name("FIT_TYPE_200") == 200
    assert fit_file_type_from_data_type_name("activity") is None
    assert is_activity_or_health_data_type_name("FIT_TYPE_4")
    assert is_activity_or_health_data_type_name("FIT_TYPE_32")
    assert is_activity_or_health_data_type_name("FIT_TYPE_49")
    assert not is_activity_or_health_data_type_name("FIT_TYPE_35")


def _fit_activity_records() -> bytes:
    import struct

    fields = bytes([
        253, 4, 0x86,  # timestamp uint32
        0, 4, 0x85,    # position_lat sint32
        2, 2, 0x84,    # altitude uint16
        3, 1, 0x02,    # heart rate uint8
        5, 4, 0x86,    # distance uint32
        6, 2, 0x84,    # speed uint16
    ])
    definition = bytes([0x40, 0, 0, 20, 0, 6]) + fields
    lat = 0x20000000  # 45 degrees in FIT semicircles
    first = bytes([0x00]) + struct.pack("<IiHB I H".replace(" ", ""), 1000, lat, 3000, 150, 12345, 2500)
    # Compressed timestamp local-message 0, time offset 1005 & 0x1f = 13.
    second = bytes([0x80 | 13]) + struct.pack("<iHB I H".replace(" ", ""), lat, 3005, 151, 12445, 2600)
    records = definition + first + second
    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    header = header12 + crc16_arc(header12).to_bytes(2, "little")
    body = header + records
    return body + crc16_arc(body).to_bytes(2, "little")


def test_generic_fit_record_decoder_and_activity_samples() -> None:
    from garmin_proto_lab.fit import extract_activity_samples, parse_fit_records

    data = _fit_activity_records()
    records = parse_fit_records(data)
    assert len(records) == 2
    assert records[0].global_message_number == 20
    assert records[0].timestamp == 1000
    assert records[1].timestamp == 1005
    assert records[1].value(253) == 1005
    assert records[1].field(253).raw == b""  # compressed header supplied the timestamp

    samples = extract_activity_samples(data)
    assert len(samples) == 2
    assert samples[0].latitude_deg == pytest.approx(45.0)
    assert samples[0].altitude_m == pytest.approx(100.0)
    assert samples[0].heart_rate_bpm == 150
    assert samples[0].distance_m == pytest.approx(123.45)
    assert samples[0].speed_mps == pytest.approx(2.5)
    assert samples[1].heart_rate_bpm == 151
    assert samples[1].timestamp_utc is not None


def test_compressed_timestamp_requires_previous_timestamp() -> None:
    data = bytearray(_fit_activity_records())
    # Replace the first normal data record with a compressed header. The parser
    # must reject it because no previous timestamp exists yet.
    definition_size = 1 + 5 + 6 * 3
    first_header = 14 + definition_size
    data[first_header] = 0x80
    # Recompute file CRC so the structural timestamp error is what is tested.
    crc_offset = int.from_bytes(data[4:8], "little") + data[0]
    data[crc_offset:crc_offset + 2] = crc16_arc(data[:crc_offset]).to_bytes(2, "little")
    from garmin_proto_lab.fit import parse_fit_records
    with pytest.raises(FitError, match="no previous timestamp"):
        parse_fit_records(bytes(data))


def test_fit_semantic_summary_is_json_ready() -> None:
    import json
    from garmin_proto_lab.fit import fit_semantic_summary

    summary = fit_semantic_summary(_fit_activity_records(), include_activity_samples=True, include_records=True)
    assert summary["file_type"] is None  # synthetic record-only file has no File ID message
    assert summary["record_count"] == 2
    assert summary["activity_sample_count"] == 2
    assert summary["message_counts"] == {"RECORD": 2}
    assert summary["activity_samples"][0]["heart_rate_bpm"] == 150
    assert summary["records"][0]["message_name"] == "RECORD"
    assert summary["records"][0]["fields"][0]["number"] == 253
    json.dumps(summary)


def test_fit_base_type_invalid_values_become_none() -> None:
    from garmin_proto_lab.fit import _decode_fit_field

    assert _decode_fit_field(b"\xff", 0x02, True) is None
    assert _decode_fit_field(b"\xff\xff", 0x84, True) is None
    assert _decode_fit_field(b"\xff\xff\xff\xff", 0x86, True) is None
    assert _decode_fit_field(b"\x00", 0x8A, True) is None  # uint8z
    assert _decode_fit_field(b"\xff" * 4, 0x88, True) is None  # float32 invalid
    assert _decode_fit_field(b"\x01\xff", 0x02, True) == (1, None)



def _fit_wellness_records() -> bytes:
    import struct

    def definition(local: int, global_message: int, fields: list[tuple[int, int, int]]) -> bytes:
        out = bytearray([0x40 | local, 0, 0])
        out += global_message.to_bytes(2, "little")
        out.append(len(fields))
        for number, size, base_type in fields:
            out += bytes((number, size, base_type))
        return bytes(out)

    records = bytearray()
    records += definition(8, 30, [(253, 4, 0x86), (0, 2, 0x84), (1, 2, 0x84), (5, 2, 0x84), (7, 2, 0x84), (10, 1, 0x02), (13, 2, 0x84)])
    records += bytes([8]) + struct.pack("<IHHHHBH", 999, 7250, 1840, 3025, 6400, 35, 231)

    records += definition(9, 51, [(253, 4, 0x86), (0, 2, 0x84), (1, 2, 0x84), (2, 2, 0x84), (6, 1, 0x02), (7, 1, 0x00), (8, 1, 0x00)])
    records += bytes([9]) + struct.pack("<IHHHBBB", 999, 118, 76, 90, 61, 0, 0)

    records += definition(0, 55, [(253, 4, 0x86), (2, 4, 0x86), (3, 4, 0x86), (5, 1, 0x00), (27, 1, 0x02), (28, 1, 0x02), (30, 4, 0x86), (31, 4, 0x86)])
    records += bytes([0]) + struct.pack("<IIIBBBII", 1000, 12345, 200, 6, 72, 15, 30, 1234)

    records += definition(1, 227, [(0, 2, 0x83), (1, 4, 0x86)])
    records += bytes([1]) + struct.pack("<hI", 42, 1001)

    records += definition(2, 269, [(253, 4, 0x86), (0, 1, 0x02), (1, 1, 0x02), (2, 1, 0x00)])
    records += bytes([2]) + struct.pack("<IBBB", 1002, 97, 80, 3)

    records += definition(3, 275, [(253, 4, 0x86), (0, 1, 0x00)])
    records += bytes([3]) + struct.pack("<IB", 1003, 4)

    records += definition(4, 297, [(253, 4, 0x86), (0, 2, 0x83)])
    records += bytes([4]) + struct.pack("<Ih", 1004, 1450)

    records += definition(5, 314, [(253, 4, 0x86), (0, 2, 0x84), (1, 1, 0x01), (2, 2, 0x83), (3, 2, 0x83)])
    records += bytes([5]) + struct.pack("<IHbhh", 1005, 300, 75, 10, 3)

    records += definition(6, 78, [(0, 6, 0x84)])
    records += bytes([6]) + struct.pack("<HHH", 800, 1000, 0xFFFF)

    records += definition(7, 211, [(253, 4, 0x86), (0, 1, 0x02), (1, 1, 0x02)])
    records += bytes([7]) + struct.pack("<IBB", 1006, 55, 57)

    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    header = header12 + crc16_arc(header12).to_bytes(2, "little")
    body = header + records
    return body + crc16_arc(body).to_bytes(2, "little")


def test_standard_wellness_semantic_projection_and_summary() -> None:
    import json
    from garmin_proto_lab.fit import extract_wellness_samples, fit_semantic_summary

    data = _fit_wellness_records()
    samples = extract_wellness_samples(data)
    by_kind = {sample.kind: sample for sample in samples}

    assert by_kind["weight_scale"].value("weight_kg") == pytest.approx(72.5)
    assert by_kind["weight_scale"].value("body_fat_percent") == pytest.approx(18.4)
    assert by_kind["weight_scale"].value("muscle_mass_kg") == pytest.approx(30.25)
    assert by_kind["weight_scale"].value("basal_metabolic_rate_kcal_per_day") == pytest.approx(1600.0)
    assert by_kind["weight_scale"].value("bmi_kg_per_m2") == pytest.approx(23.1)
    assert by_kind["blood_pressure"].value("systolic_mmhg") == 118
    assert by_kind["blood_pressure"].value("diastolic_mmhg") == 76
    assert by_kind["blood_pressure"].value("heart_rate_type") == "normal"
    assert by_kind["blood_pressure"].value("status") == "no_error"
    assert by_kind["monitoring"].value("distance_m") == pytest.approx(123.45)
    assert by_kind["monitoring"].value("cycles") == pytest.approx(100.0)
    assert by_kind["monitoring"].value("activity_type") == "walking"
    assert by_kind["monitoring"].value("heart_rate_bpm") == 72
    assert by_kind["monitoring"].value("intensity") == pytest.approx(1.5)
    assert by_kind["monitoring"].value("ascent_m") == pytest.approx(1.234)
    assert by_kind["stress"].timestamp_garmin == 1001
    assert by_kind["stress"].value("stress_level") == 42
    assert by_kind["spo2"].value("spo2_percent") == 97
    assert by_kind["spo2"].value("mode") == "periodic"
    assert by_kind["sleep_level"].value("sleep_level") == "rem"
    assert by_kind["respiration_rate"].value("respiration_rate_breaths_per_min") == pytest.approx(14.5)
    assert by_kind["body_battery"].value("body_battery_percent") == 75
    assert by_kind["hrv"].value("rr_intervals_ms") == (800.0, 1000.0, None)
    assert by_kind["monitoring_heart_rate"].value("resting_heart_rate_bpm") == 55

    summary = fit_semantic_summary(data, include_wellness_samples=True, include_records=True)
    assert summary["wellness_sample_count"] == 10
    assert {item["kind"] for item in summary["wellness_samples"]} >= {"monitoring", "weight_scale", "blood_pressure", "sleep_level"}
    assert summary["message_counts"]["SLEEP_LEVEL"] == 1
    json.dumps(summary)



def _fit_activity_summary_records() -> bytes:
    import struct

    def definition(local: int, global_message: int, fields: list[tuple[int, int, int]]) -> bytes:
        out = bytearray([0x40 | local, 0, 0])
        out += global_message.to_bytes(2, "little")
        out.append(len(fields))
        for number, size, base_type in fields:
            out += bytes((number, size, base_type))
        return bytes(out)

    records = bytearray()
    session_fields = [
        (253, 4, 0x86), (2, 4, 0x86), (5, 1, 0x00), (6, 1, 0x00),
        (7, 4, 0x86), (8, 4, 0x86), (9, 4, 0x86), (11, 2, 0x84),
        (16, 1, 0x02), (17, 1, 0x02), (20, 2, 0x84), (21, 2, 0x84),
        (22, 2, 0x84), (23, 2, 0x84), (24, 1, 0x02), (26, 2, 0x84),
        (59, 4, 0x86), (124, 4, 0x86), (125, 4, 0x86),
    ]
    records += definition(0, 18, session_fields)
    records += bytes([0]) + struct.pack(
        "<IIBBIIIHBBHHHHBHI II".replace(" ", ""),
        2000, 1000, 1, 3, 3_600_000, 3_500_000, 10_000_00, 650,
        150, 182, 240, 580, 120, 118, 35, 4, 3_200_000, 3200, 5100,
    )

    lap_fields = [
        (253, 4, 0x86), (2, 4, 0x86), (7, 4, 0x86), (8, 4, 0x86),
        (9, 4, 0x86), (11, 2, 0x84), (13, 2, 0x84), (14, 2, 0x84),
        (15, 1, 0x02), (16, 1, 0x02), (19, 2, 0x84), (20, 2, 0x84),
        (21, 2, 0x84), (22, 2, 0x84), (25, 1, 0x00), (52, 4, 0x86),
    ]
    records += definition(1, 19, lap_fields)
    records += bytes([1]) + struct.pack(
        "<IIIIIHHHBBHHHHBI",
        1500, 1000, 900_000, 880_000, 250_000, 160, 2800, 4200,
        148, 179, 230, 520, 30, 28, 1, 850_000,
    )

    activity_fields = [(253, 4, 0x86), (0, 4, 0x86), (1, 2, 0x84), (2, 1, 0x00), (5, 4, 0x86)]
    records += definition(2, 34, activity_fields)
    records += bytes([2]) + struct.pack("<IIHBI", 2100, 3_500_000, 1, 0, 2100)

    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    header = header12 + crc16_arc(header12).to_bytes(2, "little")
    body = header + records
    return body + crc16_arc(body).to_bytes(2, "little")


def test_activity_session_lap_and_activity_aggregates() -> None:
    from garmin_proto_lab.fit import extract_activity_summaries, fit_semantic_summary

    data = _fit_activity_summary_records()
    summaries = extract_activity_summaries(data)
    assert [item.kind for item in summaries] == ["session", "lap", "activity"]
    session, lap, activity = summaries
    assert session.value("sport") == "running"
    assert session.value("sub_sport") == "trail"
    assert session.value("total_elapsed_time_s") == pytest.approx(3600.0)
    assert session.value("total_timer_time_s") == pytest.approx(3500.0)
    assert session.value("total_distance_m") == pytest.approx(10000.0)
    assert session.value("total_calories_kcal") == 650
    assert session.value("average_heart_rate_bpm") == 150
    assert session.value("maximum_heart_rate_bpm") == 182
    assert session.value("average_power_w") == 240
    assert session.value("maximum_power_w") == 580
    assert session.value("training_effect") == pytest.approx(3.5)
    assert session.value("average_speed_mps") == pytest.approx(3.2)
    assert session.value("maximum_speed_mps") == pytest.approx(5.1)
    assert session.value("num_laps") == 4
    assert lap.value("sport") == "running"
    assert lap.value("total_distance_m") == pytest.approx(2500.0)
    assert lap.value("average_speed_mps") == pytest.approx(2.8)
    assert activity.value("num_sessions") == 1
    assert activity.value("activity_type") == "manual"

    summary = fit_semantic_summary(data, include_activity_summaries=True)
    assert summary["activity_summary_count"] == 3
    assert summary["activity_summaries"][0]["values"]["sport"] == "running"
