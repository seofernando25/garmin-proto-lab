#!/usr/bin/env python3
"""Safely and reproducibly unpack the APKMirror bundle used as evidence."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "garmin-connect-5.29.apkm"
DEFAULT_DEST = ROOT / "analysis" / "bundle"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(source: Path, destination: Path, fresh: bool) -> None:
    if fresh and destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    with zipfile.ZipFile(source) as zf:
        for info in sorted(zf.infolist(), key=lambda x: x.filename):
            target = (destination / info.filename).resolve()
            if base != target and base not in target.parents:
                raise ValueError(f"unsafe archive path: {info.filename!r}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()
    safe_extract(args.source, args.dest, args.fresh)
    files = sorted(p for p in args.dest.rglob("*") if p.is_file())
    print(f"source={args.source}")
    print(f"source_sha256={sha256(args.source)}")
    print(f"destination={args.dest}")
    print(f"file_count={len(files)}")
    for p in files:
        print(f"{p.relative_to(args.dest)}\t{p.stat().st_size}\t{sha256(p)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
