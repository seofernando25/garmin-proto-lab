from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.sync import (
    DownloadCategory,
    FileFlag,
    FileReady,
    QueuedDownload,
    SyncError,
    SyncOption,
    SyncRequest,
    bitset_from_flags,
    flags_from_bitset,
)


def test_sync_request_bitset_and_known_categories() -> None:
    request = SyncRequest(
        SyncOption.MANUAL,
        frozenset({DownloadCategory.ACTIVITIES, DownloadCategory.SLEEP, DownloadCategory.HRV_STATUS}),
    )
    raw = request.encode()
    assert raw[0] == 0
    assert raw[1] == 5
    parsed = SyncRequest.parse(raw)
    assert parsed == request
    assert parsed.known_categories == frozenset(
        {DownloadCategory.ACTIVITIES, DownloadCategory.SLEEP, DownloadCategory.HRV_STATUS}
    )


def test_queued_download_layout() -> None:
    queued = QueuedDownload(frozenset({0, 8, 39}))
    raw = queued.encode()
    assert raw[0] == 5
    assert QueuedDownload.parse(raw) == queued


def test_file_ready_preserves_unknown_byte_and_flags() -> None:
    item = FileReady(
        file_index=0x1234,
        data_type=4,
        identifier=bytes([32, 0xAA, 0x55]),
        unknown_6=0x9C,
        flags=FileFlag.READ | FileFlag.ARCHIVE,
        size=123456,
        timestamp=0x10203040,
    )
    raw = item.encode()
    assert len(raw) == 16
    assert raw[6] == 0x9C
    assert raw[7] == 0x90
    parsed = FileReady.parse(raw)
    assert parsed == item
    assert parsed.subtype == 32


@given(st.sets(st.integers(min_value=0, max_value=255), max_size=50))
def test_bitset_property_roundtrip(flags: set[int]) -> None:
    raw = bitset_from_flags(flags)
    assert flags_from_bitset(raw) == frozenset(flags)


def test_sync_parsers_fail_closed() -> None:
    with pytest.raises(SyncError, match="shorter"):
        SyncRequest.parse(b"\x00")
    with pytest.raises(SyncError, match="truncated"):
        SyncRequest.parse(bytes([0, 2, 1]))
    with pytest.raises(SyncError, match="empty"):
        QueuedDownload.parse(b"")
    with pytest.raises(SyncError, match="sixteen"):
        FileReady.parse(bytes(15))
    with pytest.raises(SyncError, match="one-byte"):
        bitset_from_flags({2048})
