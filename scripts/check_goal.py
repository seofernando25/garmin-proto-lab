#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path

START = "<!-- REQUIRED-CHECKLIST:START -->"
END = "<!-- REQUIRED-CHECKLIST:END -->"
REQ_RE = re.compile(r"^- \[([ xX])\] \*\*(REQ-[0-9]+)\b", re.MULTILINE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the mandatory GOAL.md completion gate")
    parser.add_argument("goal", nargs="?", default="GOAL.md")
    args = parser.parse_args()

    path = Path(args.goal)
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit("mandatory checklist markers missing")

    block = text.split(START, 1)[1].split(END, 1)[0]
    matches = REQ_RE.findall(block)
    if not matches:
        raise SystemExit("no mandatory requirements found")

    ids = [req_id for _, req_id in matches]
    dupes = sorted({req_id for req_id in ids if ids.count(req_id) > 1})
    if dupes:
        raise SystemExit(f"duplicate requirement IDs: {', '.join(dupes)}")

    pending = [req_id for mark, req_id in matches if mark == " "]
    complete = len(matches) - len(pending)
    print(f"mandatory requirements: {len(matches)}")
    print(f"complete: {complete}")
    print(f"pending: {len(pending)}")
    if pending:
        print("pending IDs: " + ", ".join(pending))
        return 1

    print("GOAL COMPLETE: every mandatory requirement is checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
