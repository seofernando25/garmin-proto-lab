"""Legacy GFDI sync/download intent codecs (5009/5027/5037).

These are request/notification payloads that select download categories and
announce concrete files. They do not guess which categories or file indexes a
specific watch will actually use.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag


class SyncError(ValueError):
    pass


class SyncOption(IntEnum):
    MANUAL = 0
    INVISIBLE = 1
    VISIBLE_AS_NEEDED = 2


class DownloadCategory(IntEnum):
    SCHEDULES = 0
    SETTINGS = 1
    GOALS = 2
    WORKOUTS = 3
    COURSES = 4
    ACTIVITIES = 5
    PERSONAL_RECORDS = 6
    UNKNOWN_TYPE = 7
    SOFTWARE_UPDATE = 8
    DEVICE_SETTINGS = 9
    LANGUAGE_SETTINGS = 10
    USER_PROFILE = 11
    SPORTS = 12
    SEGMENT_LEADERS = 13
    GOLF_CLUB = 14
    WELLNESS_DEVICE_INFO = 15
    WELLNESS_DEVICE_CCF = 16
    INSTALL_APP = 17
    CHECK_BACK = 18
    TRUE_UP = 19
    SETTINGS_CHANGE = 20
    ACTIVITY_SUMMARY = 21
    METRICS_FILE = 22
    PACE_BAND = 23
    SPORT_BACKUP = 24
    ULF_CONFIG = 25
    SLEEP = 26
    EXERCISE_BENCHMARK = 27
    POWER_GUIDANCE = 28
    SPORTING_EVENT = 29
    ACTUAL_STEP_RECORDING_CONFIGURATION = 30
    LHA_BACKUP = 31
    BBI_RECORDING_CONFIGURATION = 32
    HRV_STATUS = 36
    UNINSTALL_APP = 39


class FileFlag(IntFlag):
    READ = 0x80
    WRITE = 0x40
    ERASE = 0x20
    ARCHIVE = 0x10
    APPEND = 0x08
    CRYPTO = 0x04


def flags_from_bitset(bitset: bytes) -> frozenset[int]:
    result: set[int] = set()
    for byte_index, value in enumerate(bitset):
        for bit in range(8):
            if value & (1 << bit):
                result.add(byte_index * 8 + bit)
    return frozenset(result)


def bitset_from_flags(flags: set[int] | frozenset[int]) -> bytes:
    if not flags:
        return b""
    if min(flags) < 0:
        raise SyncError("download flag cannot be negative")
    largest = max(flags)
    if largest > 2047:
        # Length prefix is one byte, therefore at most 255 bitset bytes.
        raise SyncError("download flag cannot be represented by one-byte bitset length")
    out = bytearray(largest // 8 + 1)
    for flag in flags:
        out[flag // 8] |= 1 << (flag % 8)
    return bytes(out)


@dataclass(frozen=True, slots=True)
class SyncRequest:
    option: SyncOption | int
    download_flags: frozenset[int]

    def encode(self) -> bytes:
        option = int(self.option)
        if not 0 <= option <= 0xFF:
            raise SyncError("sync option out of byte range")
        bitset = bitset_from_flags(self.download_flags)
        return bytes([option, len(bitset)]) + bitset

    @classmethod
    def parse(cls, payload: bytes) -> "SyncRequest":
        if len(payload) < 2:
            raise SyncError("5037 sync request shorter than two bytes")
        length = payload[1]
        end = 2 + length
        if len(payload) < end:
            raise SyncError("5037 sync-request bitset is truncated")
        if len(payload) != end:
            raise SyncError("trailing bytes after 5037 sync-request bitset")
        raw_option = payload[0]
        try:
            option: SyncOption | int = SyncOption(raw_option)
        except ValueError:
            option = raw_option
        return cls(option, flags_from_bitset(payload[2:end]))

    @property
    def known_categories(self) -> frozenset[DownloadCategory]:
        known: set[DownloadCategory] = set()
        for flag in self.download_flags:
            try:
                known.add(DownloadCategory(flag))
            except ValueError:
                pass
        return frozenset(known)


@dataclass(frozen=True, slots=True)
class QueuedDownload:
    download_flags: frozenset[int]

    def encode(self) -> bytes:
        bitset = bitset_from_flags(self.download_flags)
        return bytes([len(bitset)]) + bitset

    @classmethod
    def parse(cls, payload: bytes) -> "QueuedDownload":
        if not payload:
            raise SyncError("5027 queued-download payload is empty")
        length = payload[0]
        end = 1 + length
        if len(payload) < end:
            raise SyncError("5027 queued-download bitset is truncated")
        if len(payload) != end:
            raise SyncError("trailing bytes after 5027 queued-download bitset")
        return cls(flags_from_bitset(payload[1:end]))


@dataclass(frozen=True, slots=True)
class FileReady:
    file_index: int
    data_type: int
    identifier: bytes
    reserved_6: int
    flags: FileFlag | int
    size: int
    timestamp: int

    @property
    def subtype(self) -> int:
        # The downstream static sync bridge treats identifier[0] as a subtype.
        return self.identifier[0]

    @classmethod
    def parse(cls, payload: bytes) -> "FileReady":
        if len(payload) < 16:
            raise SyncError("5009 file-ready payload shorter than sixteen bytes")
        raw_flags = payload[7]
        try:
            flags: FileFlag | int = FileFlag(raw_flags)
        except ValueError:
            flags = raw_flags
        return cls(
            file_index=int.from_bytes(payload[0:2], "little"),
            data_type=payload[2],
            identifier=bytes(payload[3:6]),
            reserved_6=payload[6],
            flags=flags,
            size=int.from_bytes(payload[8:12], "little"),
            timestamp=int.from_bytes(payload[12:16], "little"),
        )

    def encode(self) -> bytes:
        if not 0 <= self.file_index <= 0xFFFF:
            raise SyncError("file_index out of uint16 range")
        if not 0 <= self.data_type <= 0xFF:
            raise SyncError("data_type out of byte range")
        if len(self.identifier) != 3:
            raise SyncError("file-ready identifier must be exactly three bytes")
        if not 0 <= self.reserved_6 <= 0xFF:
            raise SyncError("reserved_6 out of byte range")
        flags = int(self.flags)
        if not 0 <= flags <= 0xFF:
            raise SyncError("file flags out of byte range")
        if not 0 <= self.size <= 0xFFFFFFFF or not 0 <= self.timestamp <= 0xFFFFFFFF:
            raise SyncError("file size/timestamp out of uint32 range")
        return (
            self.file_index.to_bytes(2, "little")
            + bytes([self.data_type])
            + self.identifier
            + bytes([self.reserved_6, flags])
            + self.size.to_bytes(4, "little")
            + self.timestamp.to_bytes(4, "little")
        )
