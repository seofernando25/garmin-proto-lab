from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.filetransfer import (
    DownloadRequest,
    DownloadStatus,
    FileDataStatus,
    FileTransferError,
    StandardFileReceiver,
    build_compressed_file_data,
    build_file_data,
    parse_compressed_file_data,
    parse_download_response,
    parse_file_data,
    parse_supported_file_types,
)


def test_download_request_static_layout() -> None:
    raw = DownloadRequest(0x1234, compression=True).encode()
    assert len(raw) == 14
    assert raw[:2] == bytes.fromhex("3412")
    assert raw[2:6] == bytes(4)
    assert raw[6] == 1
    assert raw[7:13] == bytes(6)
    assert raw[13] == 1


def test_download_response() -> None:
    response = parse_download_response(bytes([0]) + (123456).to_bytes(4, "little"))
    assert response.status is DownloadStatus.OK
    assert response.file_size == 123456
    assert parse_download_response(bytes([3])).status is DownloadStatus.NOT_READY


@given(st.binary(max_size=1024), st.integers(min_value=0, max_value=0xFFFFFFFF), st.integers(0, 255))
def test_file_data_build_parse(data: bytes, offset: int, marker: int) -> None:
    payload, _ = build_file_data(data, offset, previous_crc=0x1234, marker=marker)
    parsed = parse_file_data(payload)
    assert parsed.marker == marker
    assert parsed.offset == offset
    assert parsed.data == data


def test_standard_receiver_success_duplicate_offset_and_crc_error() -> None:
    receiver = StandardFileReceiver(final_size=6)
    p0, crc0 = build_file_data(b"abc", 0)
    r0 = receiver.accept(p0)
    assert r0.status is FileDataStatus.OK
    assert r0.accepted_data == b"abc"
    assert r0.response == bytes([0]) + (3).to_bytes(4, "little")

    # Duplicate prior offset is re-acked without writing data again.
    duplicate = receiver.accept(p0)
    assert duplicate.status is FileDataStatus.OK
    assert duplicate.accepted_data == b""
    assert receiver.current_offset == 3

    p1, _ = build_file_data(b"def", 3, previous_crc=crc0)
    bad = bytearray(p1)
    bad[1] ^= 0xFF
    crc_error = receiver.accept(bytes(bad))
    assert crc_error.status is FileDataStatus.CRC_MISMATCH
    assert receiver.current_offset == 3

    ok = receiver.accept(p1)
    assert ok.finished is True
    assert receiver.current_offset == 6


def test_standard_receiver_offset_mismatch() -> None:
    receiver = StandardFileReceiver(final_size=10)
    payload, _ = build_file_data(b"xx", 5)
    result = receiver.accept(payload)
    assert result.status is FileDataStatus.OFFSET_MISMATCH
    assert result.response == bytes([4]) + bytes(4)


def test_compressed_header_roundtrip() -> None:
    raw = build_compressed_file_data(
        b"compressed",
        packet_counter=255,
        decompressed_position=1234,
        running_crc=0xA55A,
        first_or_restart=True,
        end_of_stream=True,
    )
    chunk = parse_compressed_file_data(raw)
    assert chunk.packet_counter == 255
    assert chunk.flags == 3
    assert chunk.end_of_stream is True
    assert chunk.decompressed_position == 1234
    assert chunk.running_crc == 0xA55A
    assert chunk.compressed_data == b"compressed"


def test_supported_file_types() -> None:
    raw = bytes([2, 1, 2, 3]) + b"FIT" + bytes([9, 4, 3]) + b"SYS"
    rows = parse_supported_file_types(raw)
    assert [(x.data_type, x.subtype, x.name) for x in rows] == [(1, 2, "FIT"), (9, 4, "SYS")]
    with pytest.raises(FileTransferError):
        parse_supported_file_types(raw + b"x")


def test_compressed_receiver_streams_crc_counter_and_resets() -> None:
    import zlib

    from garmin_proto_lab.crc import crc16_arc
    from garmin_proto_lab.filetransfer import (
        CompressedDataStatus,
        CompressedFileReceiver,
        build_compressed_file_data,
    )

    first = b"hello " * 20
    second = b"world!" * 15
    receiver = CompressedFileReceiver(len(first) + len(second))
    crc1 = crc16_arc(first)
    packet1 = build_compressed_file_data(
        zlib.compress(first),
        packet_counter=0,
        decompressed_position=len(first),
        running_crc=crc1,
        end_of_stream=True,
    )
    result1 = receiver.accept(packet1)
    assert result1.status is CompressedDataStatus.OK
    assert result1.response == b"\x00\x00"
    assert result1.accepted_data == first
    assert result1.finished is False

    crc2 = crc16_arc(second, crc1)
    packet2 = build_compressed_file_data(
        zlib.compress(second),
        packet_counter=1,
        decompressed_position=len(first) + len(second),
        running_crc=crc2,
        end_of_stream=True,
    )
    result2 = receiver.accept(packet2)
    assert result2.status is CompressedDataStatus.OK
    assert result2.accepted_data == second
    assert result2.finished is True

    wrong_counter = receiver.accept(packet2)
    assert wrong_counter.status is CompressedDataStatus.UNEXPECTED_COUNTER
    assert wrong_counter.response == bytes([1, 4, 2])


def test_compressed_receiver_bad_crc_and_bad_deflate_fail_closed() -> None:
    import zlib

    from garmin_proto_lab.filetransfer import (
        CompressedDataStatus,
        CompressedFileReceiver,
        build_compressed_file_data,
    )

    data = b"payload"
    receiver = CompressedFileReceiver(len(data))
    bad_crc = build_compressed_file_data(
        zlib.compress(data),
        packet_counter=0,
        decompressed_position=len(data),
        running_crc=0x1234,
        end_of_stream=True,
    )
    assert receiver.accept(bad_crc).status is CompressedDataStatus.INCORRECT_CRC

    receiver = CompressedFileReceiver(len(data))
    broken = build_compressed_file_data(
        b"not-zlib",
        packet_counter=0,
        decompressed_position=len(data),
        running_crc=0,
        end_of_stream=True,
    )
    assert receiver.accept(broken).status is CompressedDataStatus.DECOMPRESS_FAILURE


def test_directory_file_filter_archive_and_legacy_types() -> None:
    from garmin_proto_lab.filetransfer import (
        DirectoryFileFlag,
        DirectoryFilter,
        build_apply_directory_filter,
        build_archive_request,
        legacy_supported_file_types,
        parse_apply_directory_filter_response,
        parse_directory_file,
    )

    header = bytes(range(16))
    entry = (
        (7).to_bytes(2, "little")
        + bytes([128, 4, 0xAA, 0x55, 0x9C, int(DirectoryFileFlag.READ | DirectoryFileFlag.ARCHIVE)])
        + (12345).to_bytes(4, "little")
        + (0x10203040).to_bytes(4, "little")
    )
    parsed = parse_directory_file(header + entry)
    assert parsed.header16 == header
    assert len(parsed.entries) == 1
    item = parsed.entries[0]
    assert item.file_index == 7
    assert item.data_type == 128
    assert item.subtype == 4
    assert item.unknown_6 == 0x9C
    assert item.flags & DirectoryFileFlag.READ
    assert item.flags & DirectoryFileFlag.ARCHIVE
    assert item.size == 12345

    assert build_apply_directory_filter(DirectoryFilter.PENDING_UPLOADS_ONLY) == b"\x03"
    assert parse_apply_directory_filter_response(b"\x00") is True
    assert parse_apply_directory_filter_response(b"\x01") is False
    assert build_archive_request(7) == b"\x07\x00\x10"
    assert [(x.data_type, x.subtype) for x in legacy_supported_file_types()] == [(128, 4), (128, 36)]

    with pytest.raises(FileTransferError, match="partial"):
        parse_directory_file(header + b"x")
