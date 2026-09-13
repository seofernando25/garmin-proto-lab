#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def require(condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def load_json(path: str):
    p = ROOT / path
    require(p.is_file(), f"missing {path}")
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        errors.append(f"invalid JSON {path}: {exc}")
        return {}


def text(path: str) -> str:
    p = ROOT / path
    require(p.is_file(), f"missing {path}")
    return p.read_text(errors="replace") if p.is_file() else ""


def main() -> int:
    artifacts = load_json("analysis/inventory/artifacts.json")
    bundle = load_json("analysis/inventory/bundle_inventory.json")
    toolchain = load_json("analysis/inventory/toolchain.json")
    manifest = load_json("analysis/inventory/manifest_map.json")
    ble = load_json("analysis/inventory/ble_call_sites.json")
    uuids = load_json("analysis/inventory/uuid_inventory.json")
    messages = load_json("analysis/inventory/message_ids.json")
    native = load_json("analysis/inventory/native_boundary.json")

    base = artifacts.get("base_apk", {})
    for key in ("sha256", "bytes", "package", "version_name", "version_code", "min_sdk", "target_sdk", "signing"):
        require(key in base, f"artifacts base missing {key}")
    require(bool(artifacts.get("bundle_extraction_date_utc")), "missing extraction date")
    require(bundle.get("apk_count") == 33, "unexpected APK count")
    require(bool(bundle.get("architectures")), "missing architecture inventory")
    require(bool(bundle.get("dpis")), "missing DPI inventory")
    require(bool(bundle.get("languages")), "missing language inventory")
    require(bool(bundle.get("dex_inventory")), "missing DEX inventory")
    require(bool(bundle.get("native_library_inventory")), "missing native inventory")
    require(bool(bundle.get("certificate_sha256_set")), "missing signing fingerprints")
    require((ROOT / "scripts/extract_bundle.py").is_file(), "missing extraction script")
    require("repro-check base.apk identical" in text("evidence/logs/bundle-reproducibility.txt"), "bundle reproducibility log incomplete")

    tools = toolchain.get("tools", {})
    for name in ("python", "uv", "java", "jadx", "apktool", "androguard", "ghidra", "rizin", "objdump", "readelf"):
        require(name in tools, f"toolchain missing {name}")
        require(bool(tools.get(name, {}).get("version")), f"toolchain missing version for {name}")

    ignore = text(".gitignore")
    for pattern in ("analysis/jadx/", "analysis/apktool/", "captures/", "*.pcap", "*.btsnoop", ".env", "session-state/", "*.apk", "*.apkm"):
        require(pattern in ignore, f".gitignore missing {pattern}")

    require("EXIT 0" in text("evidence/logs/apktool-decode.log"), "apktool successful exit not logged")
    require("EXIT 0" in text("evidence/logs/jadx-fallback-decode.log"), "JADX fallback successful exit not logged")
    require((ROOT / "analysis/apktool/AndroidManifest.xml").is_file(), "apktool manifest missing")
    require(any((ROOT / "analysis/jadx/sources").rglob("*.java")), "JADX source output missing")

    perms = {x.get("name") for x in manifest.get("relevant_permissions", [])}
    require("android.permission.BLUETOOTH_CONNECT" in perms, "manifest map missing BLUETOOTH_CONNECT")
    require("android.permission.BLUETOOTH_SCAN" in perms, "manifest map missing BLUETOOTH_SCAN")
    require(bool(manifest.get("ble_device_sync_related_components")), "manifest component map empty")

    require(len(ble.get("all_files", [])) >= 10, "BLE call-site index unexpectedly small")
    for term in ("connectGatt", "discoverServices", "requestMtu", "writeCharacteristic", "onCharacteristicChanged"):
        require(ble.get("terms", {}).get(term, {}).get("file_count", 0) > 0, f"BLE index missing {term}")

    classes = uuids.get("counts", {})
    require(sum(classes.values()) == len(uuids.get("uuids", [])), "UUID classification count mismatch")
    require(classes.get("garmin_specific", 0) > 0, "no Garmin UUIDs classified")
    require(classes.get("standard_bluetooth", 0) > 0, "no standard Bluetooth UUIDs classified")
    require(classes.get("unresolved", 0) > 0, "UUID inventory should preserve unresolved values")

    require(len(messages.get("messages", [])) >= 50, "message census unexpectedly small")
    require("Framing" in text("spec/PROTOCOL.md") or "frame grammar" in text("spec/PROTOCOL.md").lower(), "protocol framing spec missing")
    require("Authentication" in text("spec/PROTOCOL.md"), "protocol auth spec missing")
    require("File transfer" in text("spec/PROTOCOL.md"), "protocol file-transfer spec missing")

    require("native_libraries" in native, "native boundary missing library inventory")
    require(len(native.get("watch_protocol_namespace_hits", [])) == 0, "watch protocol currently crosses identified native boundary")
    static_map = text("evidence/static/STATIC_MAP.md")
    for sid in ("S-0003", "S-0004", "S-0005", "S-0006", "S-0007", "S-0008", "S-0009", "S-0011", "S-0012"):
        require(sid in static_map, f"static evidence map missing {sid}")

    if errors:
        print("STATIC CHECK FAILED")
        for err in errors:
            print(f"- {err}")
        return 1
    print("STATIC CHECK PASSED")
    print("M0/M1 evidence prerequisites are present and internally consistent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
