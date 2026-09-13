from __future__ import annotations

import pytest

from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.fit import (
    FitError,
    FitFileType,
    classify_directory_fit_subtype,
    inspect_fit,
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
    return header12 + header_crc.to_bytes(2, "little") + records + b"\x00\x00"


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
