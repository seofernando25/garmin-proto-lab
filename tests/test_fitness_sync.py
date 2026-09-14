from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.file_access_client import FileAccessChecksum, FileAccessListing
from garmin_proto_lab.file_access_proto import (
    ChecksumMethod,
    DataTypeFormat,
    FileAccessCapabilities,
    FileDataType,
    FileItemReference,
    ItemUrgency,
    truncated_md5,
)
from garmin_proto_lab.fit import FitFileType
from garmin_proto_lab.fitness_sync import FitnessFileStore, NextGenFitnessSync


def _fit_file(file_type: int = int(FitFileType.ACTIVITY)) -> bytes:
    records = bytes([
        0x40, 0, 0, 0, 0, 1,
        0, 1, 0,
        0x00, file_type,
    ])
    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    body = header12 + crc16_arc(header12).to_bytes(2, "little") + records
    return body + crc16_arc(body).to_bytes(2, "little")


class FakeControl:
    def __init__(self, item: FileItemReference, name: str) -> None:
        self.item = item
        self.name = name
        self.calls = 0

    async def list_items(self, **kwargs):
        self.calls += 1
        return FileAccessListing((self.item,), None, 1, (self.name,))

    async def verify_item_checksum(self, item, data):
        assert item == self.item
        value = truncated_md5(data)
        return FileAccessChecksum(value, value)


class FakeDownloader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.download_calls = 0
        self.resume_calls = 0

    async def download(self, item, **kwargs):
        self.download_calls += 1
        callback = kwargs.get("on_data")
        midpoint = len(self.data) // 2
        if callback:
            callback(self.data[:midpoint])
            callback(self.data[midpoint:])
        checksum = FileAccessChecksum(truncated_md5(self.data), truncated_md5(self.data)) if kwargs.get("verify_checksum") else None
        return SimpleNamespace(data=self.data, checksum=checksum, start_offset=0)

    async def resume(self, item, prefix, **kwargs):
        self.resume_calls += 1
        assert self.data.startswith(prefix)
        callback = kwargs.get("on_data")
        if callback:
            callback(self.data[len(prefix):])
        checksum = FileAccessChecksum(truncated_md5(self.data), truncated_md5(self.data)) if kwargs.get("verify_checksum") else None
        return SimpleNamespace(data=self.data, checksum=checksum, start_offset=len(prefix))


def _item(data: bytes) -> FileItemReference:
    return FileItemReference(
        UUID(int=0x1234),
        FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "FIT_TYPE_4"),
        len(data),
        urgency=ItemUrgency.NORMAL,
    )


def test_fitness_sync_persists_valid_fit_and_skips_completed_file(tmp_path) -> None:
    async def run() -> None:
        data = _fit_file()
        item = _item(data)
        control = FakeControl(item, "FIT_TYPE_4")
        downloader = FakeDownloader(data)
        caps = FileAccessCapabilities(server_file_checksum_method=ChecksumMethod.TRUNCATED_MD5, checksum_max_file_size_byte=10_000)
        sync = NextGenFitnessSync(control, downloader, tmp_path, capabilities=caps)  # type: ignore[arg-type]
        first = await sync.sync()
        assert len(first) == 1
        assert first[0].path.read_bytes() == data
        assert first[0].checksum_verified
        assert first[0].inspection.file_type is FitFileType.ACTIVITY
        assert downloader.download_calls == 1

        second = await sync.sync()
        assert len(second) == 1
        assert second[0].already_present
        assert downloader.download_calls == 1

    asyncio.run(run())


def test_fitness_sync_resumes_existing_partial_prefix(tmp_path) -> None:
    async def run() -> None:
        data = _fit_file()
        item = _item(data)
        control = FakeControl(item, "FIT_TYPE_4")
        downloader = FakeDownloader(data)
        store = FitnessFileStore(tmp_path)
        final, partial = store.paths(item, "FIT_TYPE_4")
        prefix = data[: len(data) // 3]
        partial.write_bytes(prefix)

        sync = NextGenFitnessSync(control, downloader, tmp_path)  # type: ignore[arg-type]
        result = await sync.sync()
        assert result[0].resumed_from == len(prefix)
        assert downloader.resume_calls == 1
        assert final.read_bytes() == data
        assert not partial.exists()

    asyncio.run(run())


def test_fitness_sync_discards_corrupt_complete_partial_and_redownloads(tmp_path) -> None:
    async def run() -> None:
        data = _fit_file()
        item = _item(data)
        control = FakeControl(item, "FIT_TYPE_4")
        downloader = FakeDownloader(data)
        store = FitnessFileStore(tmp_path)
        final, partial = store.paths(item, "FIT_TYPE_4")
        corrupt = bytearray(data)
        corrupt[-1] ^= 0x80
        partial.write_bytes(bytes(corrupt))

        sync = NextGenFitnessSync(control, downloader, tmp_path)  # type: ignore[arg-type]
        result = await sync.sync()
        assert downloader.download_calls == 1
        assert downloader.resume_calls == 0
        assert result[0].resumed_from == 0
        assert final.read_bytes() == data
        assert not partial.exists()

    asyncio.run(run())


def test_fitness_sync_publishes_complete_partial_after_checksum_when_advertised(tmp_path) -> None:
    async def run() -> None:
        data = _fit_file()
        item = _item(data)
        control = FakeControl(item, "FIT_TYPE_4")
        downloader = FakeDownloader(data)
        store = FitnessFileStore(tmp_path)
        final, partial = store.paths(item, "FIT_TYPE_4")
        partial.write_bytes(data)
        caps = FileAccessCapabilities(
            server_file_checksum_method=ChecksumMethod.TRUNCATED_MD5,
            checksum_max_file_size_byte=10_000,
        )
        sync = NextGenFitnessSync(control, downloader, tmp_path, capabilities=caps)  # type: ignore[arg-type]
        result = await sync.sync()
        assert result[0].checksum_verified
        assert downloader.download_calls == 0
        assert downloader.resume_calls == 0
        assert final.read_bytes() == data

    asyncio.run(run())
