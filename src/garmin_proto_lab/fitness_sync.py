"""Persistent next-generation fitness-file synchronization.

This layer turns FileAccess item discovery plus the MultiLink/MLR downloader into
an application workflow: select activity/health FIT objects, resume interrupted
pulls from on-disk prefixes, verify FileAccess truncated-MD5 when advertised,
validate the FIT container CRC, and atomically publish complete files.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from typing import Protocol

from .file_access_client import FileAccessControlClient, FileAccessMlrDownloader
from .file_access_proto import ChecksumMethod, FileAccessCapabilities, FileItemReference
from .fit import FitInspection, inspect_fit, is_activity_or_health_data_type_name


class FitnessSyncError(RuntimeError):
    pass


class Downloader(Protocol):
    async def download(self, item: FileItemReference, **kwargs): ...
    async def resume(self, item: FileItemReference, prefix: bytes, **kwargs): ...


@dataclass(frozen=True, slots=True)
class FitnessSyncResult:
    item: FileItemReference
    data_type_name: str
    path: Path
    inspection: FitInspection
    resumed_from: int
    checksum_verified: bool
    already_present: bool = False


@dataclass(slots=True)
class FitnessFileStore:
    root: Path

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.root, 0o700)
        except OSError:
            pass

    @staticmethod
    def _safe_label(name: str) -> str:
        safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
        return safe or "fit"

    @staticmethod
    def _item_token(item: FileItemReference) -> str:
        if item.uid is None:
            raise FitnessSyncError("fitness item has no UID")
        return hashlib.sha256(str(item.uid).encode("ascii")).hexdigest()[:16]

    def paths(self, item: FileItemReference, data_type_name: str) -> tuple[Path, Path]:
        stem = f"{self._safe_label(data_type_name)}-{self._item_token(item)}"
        final = self.root / f"{stem}.fit"
        partial = self.root / f"{stem}.fit.part"
        return final, partial

    @staticmethod
    def read_prefix(path: Path, expected_size: int) -> bytes:
        if not path.exists():
            return b""
        data = path.read_bytes()
        if len(data) > expected_size:
            path.unlink()
            return b""
        return data

    @staticmethod
    def open_partial(path: Path, *, append: bool):
        mode = "ab" if append else "wb"
        handle = path.open(mode)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return handle

    @staticmethod
    def publish(partial: Path, final: Path) -> None:
        os.replace(partial, final)
        try:
            os.chmod(final, 0o600)
        except OSError:
            pass


class NextGenFitnessSync:
    def __init__(
        self,
        control: FileAccessControlClient,
        downloader: Downloader,
        output_dir: str | os.PathLike[str],
        *,
        capabilities: FileAccessCapabilities | None = None,
        request_compression: bool = False,
    ) -> None:
        self.control = control
        self.downloader = downloader
        self.store = FitnessFileStore(output_dir)
        self.capabilities = capabilities
        self.request_compression = request_compression

    def _checksum_supported(self, item: FileItemReference) -> bool:
        caps = self.capabilities
        if caps is None or caps.server_file_checksum_method is not ChecksumMethod.TRUNCATED_MD5:
            return False
        if item.data_size is None:
            return False
        maximum = caps.checksum_max_file_size_byte
        return maximum is None or item.data_size <= maximum

    @staticmethod
    def _validate_fitness_fit(data: bytes, expected_name: str) -> FitInspection:
        inspection = inspect_fit(data)
        if not inspection.is_activity_or_health:
            raise FitnessSyncError(
                f"FileAccess item {expected_name} downloaded a FIT file outside the activity/health family"
            )
        return inspection

    async def sync(self, *, limit: int | None = None) -> tuple[FitnessSyncResult, ...]:
        if limit is not None and limit < 0:
            raise ValueError("sync limit cannot be negative")
        listing = await self.control.list_items(requested_modified_time=True)
        candidates = [
            (item, name)
            for item, name in zip(listing.items, listing.data_type_names, strict=False)
            if name is not None and is_activity_or_health_data_type_name(name)
        ]
        if limit is not None:
            candidates = candidates[:limit]

        results: list[FitnessSyncResult] = []
        for item, name in candidates:
            assert name is not None
            if item.data_size is None:
                raise FitnessSyncError(f"FileAccess item {name} has no data size")
            final, partial = self.store.paths(item, name)

            if final.exists() and final.stat().st_size == item.data_size:
                try:
                    inspection = self._validate_fitness_fit(final.read_bytes(), name)
                except Exception:
                    final.unlink()
                else:
                    results.append(
                        FitnessSyncResult(item, name, final, inspection, item.data_size, False, True)
                    )
                    continue

            prefix = self.store.read_prefix(partial, item.data_size)
            verify_checksum = self._checksum_supported(item)
            if len(prefix) == item.data_size:
                try:
                    inspection = self._validate_fitness_fit(prefix, name)
                    checksum = await self.control.verify_item_checksum(item, prefix) if verify_checksum else None
                except Exception:
                    partial.unlink(missing_ok=True)
                    prefix = b""
                else:
                    self.store.publish(partial, final)
                    results.append(
                        FitnessSyncResult(item, name, final, inspection, len(prefix), checksum is not None)
                    )
                    continue

            handle = self.store.open_partial(partial, append=bool(prefix))
            try:
                def persist(chunk: bytes) -> None:
                    handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())

                if prefix:
                    transfer = await self.downloader.resume(
                        item,
                        prefix,
                        verify_checksum=verify_checksum,
                        on_data=persist,
                    )
                else:
                    transfer = await self.downloader.download(
                        item,
                        request_compression=self.request_compression,
                        verify_checksum=verify_checksum,
                        on_data=persist,
                    )
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()

            if transfer.data != partial.read_bytes():
                raise FitnessSyncError("persisted partial bytes differ from completed FileAccess transfer")
            inspection = self._validate_fitness_fit(transfer.data, name)
            if transfer.checksum is not None and not transfer.checksum.matches:
                raise FitnessSyncError("FileAccess checksum verification failed")
            self.store.publish(partial, final)
            results.append(
                FitnessSyncResult(
                    item,
                    name,
                    final,
                    inspection,
                    transfer.start_offset,
                    transfer.checksum is not None,
                )
            )
        return tuple(results)
