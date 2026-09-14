from __future__ import annotations

import asyncio

from garmin_proto_lab.client import GarminClient, NotificationPolicy, SemanticKind
from garmin_proto_lab.crc import crc16_arc
from garmin_proto_lab.file_client import DirectoryListing, FileReadResult, ListedFile
from garmin_proto_lab.filetransfer import DirectoryEntry, DirectoryFile, DirectoryFileFlag, DirectoryFilter
from garmin_proto_lab.fit import FitFileType
from garmin_proto_lab.handshake import HostIdentity


def _fit_file(file_type: int) -> bytes:
    records = bytes([
        0x40, 0, 0, 0, 0, 1,
        0, 1, 0,
        0x00, file_type,
    ])
    header12 = bytes([14, 0x20]) + (100).to_bytes(2, "little") + len(records).to_bytes(4, "little") + b".FIT"
    body = header12 + crc16_arc(header12).to_bytes(2, "little") + records
    return body + crc16_arc(body).to_bytes(2, "little")


class DummyLink:
    def __init__(self) -> None:
        self.callback = None

    def set_incoming_callback(self, callback) -> None:
        self.callback = callback


class StubFiles:
    def __init__(self, link: DummyLink, listing: DirectoryListing, data: bytes) -> None:
        self.link = link
        self.listing = listing
        self.data = data
        self.active = False
        self.archived: list[int] = []

    async def handle(self, frame) -> bool:
        return False

    async def list_directory(self, filter_value=DirectoryFilter.NO_FILTER):
        return self.listing

    async def read_file(self, file_index: int, *, compression=True, fallback_uncompressed=True):
        return FileReadResult(file_index, self.data, len(self.data), 0.01, compression, None)

    async def archive_file(self, file_index: int):
        self.archived.append(file_index)


def _listed(index: int, subtype: int, flags: int) -> ListedFile:
    return ListedFile(
        DirectoryEntry(index, 128, bytes([subtype, 0, 0]), 0xA5, flags, 100, 200),
        f"FIT_TYPE_{subtype}",
    )


def test_activity_health_semantic_api_filters_reads_crosschecks_and_archives() -> None:
    async def run() -> None:
        activity = _listed(7, FitFileType.ACTIVITY, int(DirectoryFileFlag.READ))
        archived_activity = _listed(
            8,
            FitFileType.ACTIVITY,
            int(DirectoryFileFlag.READ | DirectoryFileFlag.ARCHIVE),
        )
        workout = _listed(9, FitFileType.WORKOUT, int(DirectoryFileFlag.READ))
        listing = DirectoryListing(True, (), DirectoryFile(bytes(16), (activity.entry, archived_activity.entry, workout.entry)), (activity, archived_activity, workout))
        link = DummyLink()
        files = StubFiles(link, listing, _fit_file(FitFileType.ACTIVITY))
        client = GarminClient(
            link,  # type: ignore[arg-type]
            HostIdentity(1, "Host", "Maker", "Model"),
            frozenset(),
            notification_policy=NotificationPolicy(),
            file_transfer=files,  # type: ignore[arg-type]
        )

        candidates = await client.list_activity_health_files()
        assert candidates == (activity,)
        transfer = await client.download_activity_health(activity, archive_after_success=True)
        assert transfer.fit.file_type is FitFileType.ACTIVITY
        assert transfer.fit.is_activity_or_health
        assert files.archived == [7]

        events = [await client.events.get() for _ in range(2)]
        assert events[0].kind is SemanticKind.DIRECTORY
        assert events[0].accessible_text() == "Watch file directory received"
        assert events[1].kind is SemanticKind.FILE_READ
        assert events[1].accessible_text() == "Watch file transfer complete"

    asyncio.run(run())
