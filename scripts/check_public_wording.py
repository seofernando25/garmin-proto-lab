#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BANNED = (
    "experimental",
    "best-effort",
    "conservative",
    "tentative",
    "provisional",
    "appears",
    "seems",
    "likely",
    "probably",
    "possibly",
    "unverified",
    "speculative",
    "clean-room",
    "clean room",
)
TARGETS = [
    ROOT / "README.md",
    ROOT / "GOAL.md",
    ROOT / "docs",
    ROOT / "spec",
    ROOT / "src",
    ROOT / "tests",
    ROOT / "evidence" / "static",
]
PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(term) for term in BANNED) + r")\b", re.IGNORECASE)


def files() -> list[Path]:
    out: list[Path] = []
    for target in TARGETS:
        if target.is_file():
            out.append(target)
        elif target.is_dir():
            out.extend(p for p in target.rglob("*") if p.suffix in {".py", ".md"})
    return sorted(set(out))


def main() -> int:
    failures: list[str] = []
    for path in files():
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            match = PATTERN.search(line)
            if match:
                failures.append(f"{path.relative_to(ROOT)}:{line_no}: {match.group(0)!r}: {line.strip()}")
    if failures:
        print("PUBLIC WORDING CHECK FAILED")
        print("\n".join(failures))
        return 1
    print("PUBLIC WORDING CHECK PASSED")
    print(f"scanned_files={len(files())} banned_terms={len(BANNED)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
