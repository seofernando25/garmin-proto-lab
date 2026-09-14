#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = (ROOT / "spec" / "PROTOCOL.md").read_text(encoding="utf-8")
API = (ROOT / "docs" / "API.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
HARDWARE = (ROOT / "docs" / "HARDWARE.md").read_text(encoding="utf-8")

REQUIRED_PROTOCOL_MARKERS = (
    "P-0001", "P-0014", "P-0100", "P-0110", "P-0201", "P-0213",
    "P-0300", "P-0400", "P-0412", "P-0500", "P-0600",
    "P-0700", "P-0701", "P-0702", "P-0703", "P-0704", "P-0705", "P-0706",
    "Second-client implementation sequence",
    "fitness-sync",
)
REQUIRED_API_MARKERS = (
    "two authentication routes",
    "--system-bond",
    "FileAccessMlrDownloader",
    "NextGenFitnessSync",
    "truncated-MD5",
    "fitness-sync",
)
REQUIRED_README_MARKERS = ("garmin-proto reset-pairing", "garmin-proto pair", "garmin-proto fitness-sync", "--fit-json", "resumable `.part`", "full-file CRC")
REQUIRED_HARDWARE_MARKERS = ("reset-pairing", "5101–5111", "MultiLink", "interrupted FileAccess pull", "Garmin Connect stopped or absent")
REQUIRED_TEST_IDS = ("T-0070", "T-0071", "T-0072", "T-0073", "T-0074", "T-0075", "T-0076", "T-0077", "T-0078", "T-0079", "T-0080", "T-0081", "T-0082")


def require(text: str, markers: tuple[str, ...], label: str, failures: list[str]) -> None:
    for marker in markers:
        if marker not in text:
            failures.append(f"{label} missing {marker!r}")


def main() -> int:
    failures: list[str] = []
    require(PROTOCOL, REQUIRED_PROTOCOL_MARKERS, "spec/PROTOCOL.md", failures)
    require(PROTOCOL, REQUIRED_TEST_IDS, "spec/PROTOCOL.md test map", failures)
    require(API, REQUIRED_API_MARKERS, "docs/API.md", failures)
    require(README, REQUIRED_README_MARKERS, "README.md", failures)
    require(HARDWARE, REQUIRED_HARDWARE_MARKERS, "docs/HARDWARE.md", failures)
    if failures:
        print("PROTOCOL DOCUMENTATION CHECK FAILED")
        print("\n".join(failures))
        return 1
    print("PROTOCOL DOCUMENTATION CHECK PASSED")
    print(f"protocol_markers={len(REQUIRED_PROTOCOL_MARKERS)} test_ids={len(REQUIRED_TEST_IDS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
