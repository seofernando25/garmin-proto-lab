#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"

COMMANDS = (
    ([str(PYTHON), "-m", "pytest", "-q"], "unit/property suite"),
    ([sys.executable, "scripts/static_map.py"], "static inventory refresh"),
    ([str(PYTHON), "scripts/check_static_requirements.py"], "static evidence gate"),
    ([sys.executable, "scripts/check_required_unknowns.py"], "required-workflow unknown gate"),
    ([sys.executable, "scripts/check_public_wording.py"], "public wording gate"),
    ([sys.executable, "scripts/check_protocol_docs.py"], "protocol documentation gate"),
)


def main() -> int:
    if not PYTHON.is_file():
        print("OFFLINE COMPLETION CHECK FAILED: .venv Python is missing")
        return 1
    for command, label in COMMANDS:
        print(f"== {label} ==", flush=True)
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            print(f"OFFLINE COMPLETION CHECK FAILED at {label}")
            return result.returncode
    print("OFFLINE COMPLETION CHECK PASSED")
    print("All non-hardware pairing/fitness extraction gates are green.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
