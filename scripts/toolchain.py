#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "analysis" / "inventory" / "toolchain.json"


def run(argv: list[str]) -> str | None:
    try:
        p = subprocess.run(argv, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    text = p.stdout.strip()
    return text if text else None


def first_line(text: str | None) -> str | None:
    return text.splitlines()[0] if text else None


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def command_info(name: str, version_argv: list[str], acquisition: str) -> dict:
    path = shutil.which(name)
    return {
        "path": path,
        "version": first_line(run(version_argv)) if path else None,
        "acquisition": acquisition,
        "used": bool(path),
    }


def main() -> None:
    tools = {
        "python": {
            "path": shutil.which("python3"),
            "version": platform.python_version(),
            "acquisition": "system preinstalled; uv creates the project virtual environment",
            "used": True,
        },
        "uv": command_info("uv", ["uv", "--version"], "user-local preinstalled"),
        "java": command_info("java", ["java", "-version"], "system preinstalled OpenJDK"),
        "jadx": {
            "path": str(ROOT / "tools/bin/jadx"),
            "version": first_line(run([str(ROOT / "tools/bin/jadx"), "--version"])),
            "acquisition": "official GitHub release v1.5.6, unpacked project-locally",
            "source": "https://github.com/skylot/jadx/releases/download/v1.5.6/jadx-1.5.6.zip",
            "archive_sha256": sha256(ROOT / "tools/downloads/jadx-1.5.6.zip"),
            "used": True,
        },
        "apktool": {
            "path": str(ROOT / "tools/bin/apktool"),
            "version": first_line(run([str(ROOT / "tools/bin/apktool"), "--version"])),
            "acquisition": "official Apktool GitHub release v3.0.3 JAR, project-local wrapper",
            "source": "https://github.com/iBotPeaches/Apktool/releases/download/v3.0.3/apktool_3.0.3.jar",
            "jar_sha256": sha256(ROOT / "tools/downloads/apktool_3.0.3.jar"),
            "used": True,
        },
        "androguard": {
            "path": str(ROOT / ".venv"),
            "version": importlib.metadata.version("androguard"),
            "acquisition": "uv development dependency, locked in uv.lock",
            "used": True,
        },
        "ghidra": {"path": shutil.which("ghidra"), "version": first_line(run(["pacman", "-Q", "ghidra"])), "acquisition": "system preinstalled package", "used": bool(shutil.which("ghidra"))},
        "rizin": command_info("rizin", ["rizin", "-v"], "system preinstalled"),
        "objdump": command_info("objdump", ["objdump", "--version"], "system preinstalled binutils"),
        "readelf": command_info("readelf", ["readelf", "--version"], "system preinstalled binutils"),
        "adb": command_info("adb", ["adb", "version"], "not installed at initial inventory"),
        "tshark": command_info("tshark", ["tshark", "--version"], "not installed at initial inventory"),
        "wireshark": command_info("wireshark", ["wireshark", "--version"], "not installed at initial inventory"),
    }
    data = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "os_release": platform.freedesktop_os_release() if hasattr(platform, "freedesktop_os_release") else {},
        },
        "tools": tools,
        "notes": [
            "adb/platform-tools are not required for static analysis and were not used at this stage.",
            "Wireshark/tshark are not required for static analysis and were not used at this stage.",
            "Ghidra/rizin/binutils are recorded because they are available for native-boundary inspection.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for k, v in tools.items():
        print(f"{k}: {v.get('version') or 'NOT INSTALLED'}")


if __name__ == "__main__":
    main()
