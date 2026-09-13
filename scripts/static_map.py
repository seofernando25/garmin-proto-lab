#!/usr/bin/env python3
"""Generate deterministic static-analysis indexes from decoded/decompiled Garmin Connect."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "analysis" / "jadx" / "sources"
APKTOOL = ROOT / "analysis" / "apktool"
OUT = ROOT / "analysis" / "inventory"
A = "{http://schemas.android.com/apk/res/android}"

BLE_TERMS = [
    "BluetoothGatt",
    "BluetoothGattCallback",
    "BluetoothGattCharacteristic",
    "BluetoothGattDescriptor",
    "BluetoothGattService",
    "BluetoothLeScanner",
    "ScanFilter",
    "ScanSettings",
    "connectGatt",
    "discoverServices",
    "requestMtu",
    "createBond",
    "setCharacteristicNotification",
    "writeCharacteristic",
    "readCharacteristic",
    "writeDescriptor",
    "onConnectionStateChange",
    "onServicesDiscovered",
    "onCharacteristicChanged",
    "onCharacteristicRead",
    "onCharacteristicWrite",
    "onDescriptorWrite",
    "onMtuChanged",
]

UUID_RE = re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
GARMIN_SUFFIX = "-667B-11E3-949A-0800200C9A66"
BT_BASE = re.compile(r"^0000[0-9A-F]{4}-0000-1000-8000-00805F9B34FB$")
GARMIN_SPECIAL = {
    "9B012401-BC30-CE9A-E111-0F67E491ABDE",
    "16AA8022-3769-4C74-A755-877DDE3A2930",
    "DF334C80-E6A7-D082-274D-78FC66F85E16",
    "4ACBCD28-7425-868E-F447-915C8F00D0CB",
}
UNRELATED_PREFIXES = (
    "analysis/jadx/sources/androidx/",
    "analysis/jadx/sources/com/google/",
    "analysis/jadx/sources/okhttp3/",
    "analysis/jadx/sources/com/mapbox/",
)

# Names embedded in Garmin's own message-ID logger. This is evidence, not runtime code.
MESSAGE_IDS = {
    5000: "Acknowledgement",
    5002: "Download File",
    5003: "Upload File",
    5004: "File Data",
    5005: "Create File",
    5006: "Delete File",
    5007: "Directory Filter",
    5008: "Set File Flags",
    5009: "File Ready",
    5011: "FIT Definition",
    5012: "FIT Data",
    5014: "Weather Request",
    5015: "Weather Alert",
    5016: "LT Tracking Request",
    5019: "Ephemeris Data Request",
    5020: "Ephemeris Data",
    5022: "Cancel File Transfer",
    5023: "Battery Status",
    5024: "Device Information",
    5025: "LT Stop Tracking",
    5026: "Set Device Settings",
    5027: "Queued Download",
    5028: "Ephemeris EPO Request",
    5029: "Ephemeris EPO Data",
    5030: "System Event",
    5031: "Supported File Types",
    5033: "GNCS Notification Source",
    5034: "GNCS Control Point",
    5035: "GNCS Data Source",
    5036: "GNCS Notification Service Subscription",
    5037: "Sync Request",
    5039: "Find My Phone",
    5040: "Cancel Find My Phone",
    5041: "Music Control",
    5042: "Music Control Capabilities",
    5043: "Protobuf Request",
    5044: "Protobuf Response",
    5045: "Cancel Protobuf",
    5046: "LT Auto Start Tracking",
    5047: "LT Auto Start Cancel",
    5048: "LT Auto Start Failure",
    5049: "Music Entity Update",
    5050: "Configuration",
    5052: "Current Time",
    5054: "Compressed File Data",
    5101: "Auth Negotiation Begin",
    5102: "LTK Reconnect",
    5103: "STK Begin Generation",
    5104: "Confirm Number",
    5105: "STK Rand Number",
    5106: "STK Generation Status",
    5107: "LTK Key Distribution",
    5108: "Session Key SKD Distribution",
    5109: "Session Key Verification",
    5110: "Passkey Redisplay",
    5111: "Secure Session",
    5112: "Out-of-Band Passkey Data",
}


def rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def text_files():
    for p in SRC.rglob("*.java"):
        try:
            yield p, p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue


def manifest_map() -> dict:
    root = ET.parse(APKTOOL / "AndroidManifest.xml").getroot()
    permissions = []
    for e in root.findall("uses-permission"):
        name = e.attrib.get(A + "name")
        if not name:
            continue
        record = {"name": name}
        for attr in ("maxSdkVersion", "usesPermissionFlags"):
            if A + attr in e.attrib:
                record[attr] = e.attrib[A + attr]
        permissions.append(record)
    features = []
    for e in root.findall("uses-feature"):
        name = e.attrib.get(A + "name")
        if name:
            features.append({
                "name": name,
                "required": e.attrib.get(A + "required"),
            })
    app = root.find("application")
    components = []
    if app is not None:
        for kind in ("activity", "activity-alias", "service", "receiver", "provider"):
            for e in app.findall(kind):
                raw = ET.tostring(e, encoding="unicode").lower()
                if any(k in raw for k in ("bluetooth", "ble", "pair", "gatt", "device", "connectiq", "sync")):
                    components.append({
                        "kind": kind,
                        "name": e.attrib.get(A + "name"),
                        "exported": e.attrib.get(A + "exported"),
                        "foregroundServiceType": e.attrib.get(A + "foregroundServiceType"),
                    })
    relevant = [
        p for p in permissions if any(
            key in p["name"] for key in (
                "BLUETOOTH", "LOCATION", "INTERNET", "FOREGROUND_SERVICE",
                "EXTERNAL_STORAGE", "READ_MEDIA", "NOTIFICATION", "WAKE_LOCK",
                "COMPANION"
            )
        )
    ]
    return {
        "package": root.attrib.get("package"),
        "relevant_permissions": relevant,
        "bluetooth_features": [x for x in features if "bluetooth" in x["name"].lower()],
        "ble_device_sync_related_components": sorted(components, key=lambda x: (x["kind"], x["name"] or "")),
    }


def classify_uuid(uuid: str, paths: list[str]) -> tuple[str, str]:
    if BT_BASE.match(uuid):
        return "standard_bluetooth", "Bluetooth Base UUID form (16-bit assigned-number namespace)"
    if uuid.endswith(GARMIN_SUFFIX) or uuid in GARMIN_SPECIAL:
        return "garmin_specific", "Garmin BLE/GFDI service-family predicate or Garmin service registry"
    if paths and all(p.startswith(UNRELATED_PREFIXES) for p in paths):
        return "unrelated", "Only referenced in third-party framework/library namespace"
    return "unresolved", "Not enough static evidence to safely classify as watch protocol or unrelated"


def scan_sources() -> tuple[dict, dict, dict, dict, int]:
    ble_by_term: dict[str, dict[str, list[dict]]] = {t: defaultdict(list) for t in BLE_TERMS}
    uuid_found: dict[str, list[dict]] = defaultdict(list)
    message_refs: dict[int, list[dict]] = {mid: [] for mid in MESSAGE_IDS}
    load_hits: list[dict] = []
    native_method_hits: list[dict] = []
    protocolish_hits: list[dict] = []
    mid_re = re.compile(r"(?<!\\d)(" + "|".join(str(x) for x in sorted(MESSAGE_IDS, reverse=True)) + r")(?!\\d)")
    ble_trigger = re.compile(r"Bluetooth|connectGatt|discoverServices|requestMtu|createBond|setCharacteristicNotification|writeCharacteristic|readCharacteristic|writeDescriptor|onConnectionStateChange|onServicesDiscovered|onCharacteristicChanged|onCharacteristicRead|onCharacteristicWrite|onDescriptorWrite|onMtuChanged")
    protocol_terms = ("gfdi", "device/ble", "device/pair", "/yg2/", "/aq2/", "/bq2/", "/dq2/", "/lq2/", "/x72/")
    count = 0
    for p in SRC.rglob("*.java"):
        try:
            s = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        count += 1
        rp = rel(p)
        is_message_name_source = rp.endswith("lq2/C33761c.java")
        for i, line in enumerate(s.splitlines(), 1):
            if ble_trigger.search(line):
                for term in BLE_TERMS:
                    if term in line:
                        hits = ble_by_term[term][rp]
                        if len(hits) < 25:
                            hits.append({"line": i, "text": line.strip()[:260]})
            for m in UUID_RE.finditer(line):
                uuid_found[m.group(0).upper()].append({"file": rp, "line": i, "text": line.strip()[:300]})
            if not is_message_name_source:
                for m in mid_re.finditer(line):
                    mid = int(m.group(1))
                    if len(message_refs[mid]) < 40:
                        message_refs[mid].append({"file": rp, "line": i, "text": line.strip()[:260]})
            if "System.loadLibrary" in line or "System.load(" in line:
                hit = {"file": rp, "line": i, "text": line.strip()[:300]}
                load_hits.append(hit)
                if any(t in rp.lower() for t in protocol_terms):
                    protocolish_hits.append(hit)
            if " native " in f" {line} " and line.rstrip().endswith(";"):
                hit = {"file": rp, "line": i, "text": line.strip()[:300]}
                native_method_hits.append(hit)
                if any(t in rp.lower() for t in protocol_terms):
                    protocolish_hits.append(hit)

    all_ble_files = set()
    terms_out = {}
    for term, by_file in ble_by_term.items():
        rows = []
        for rp in sorted(by_file):
            hits = by_file[rp]
            rows.append({"file": rp, "hits": hits, "hit_count_capped": len(hits)})
            all_ble_files.add(rp)
        terms_out[term] = {"file_count": len(rows), "files": rows}
    ble = {"terms": terms_out, "all_files": sorted(all_ble_files)}

    rows = []
    counts = defaultdict(int)
    for uuid in sorted(uuid_found):
        paths = sorted({h["file"] for h in uuid_found[uuid]})
        classification, rationale = classify_uuid(uuid, paths)
        counts[classification] += 1
        rows.append({"uuid": uuid, "classification": classification, "rationale": rationale, "references": uuid_found[uuid]})
    uuids = {"counts": dict(sorted(counts.items())), "uuids": rows}

    messages = {
        "source_of_names": "analysis/jadx/sources/lq2/C33761c.java",
        "messages": [{"id": mid, "name": MESSAGE_IDS[mid], "references": message_refs[mid]} for mid in sorted(MESSAGE_IDS)],
    }
    bundle = json.loads((OUT / "bundle_inventory.json").read_text())
    native = {
        "native_libraries": bundle.get("native_library_inventory", []),
        "system_load_hits": load_hits,
        "native_method_hits": native_method_hits,
        "watch_protocol_namespace_hits": protocolish_hits,
    }
    return ble, uuids, messages, native, count

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    ble, uuids, messages, native, count = scan_sources()
    reports = {
        "manifest_map.json": manifest_map(),
        "ble_call_sites.json": ble,
        "uuid_inventory.json": uuids,
        "message_ids.json": messages,
        "native_boundary.json": native,
    }
    for name, data in reports.items():
        (OUT / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"java_files={count}")
    print(f"ble_files={len(ble['all_files'])}")
    print(f"unique_uuids={len(uuids['uuids'])} {uuids['counts']}")
    print(f"message_ids={len(messages['messages'])}")
    print(f"native_lib_paths={len(native['native_libraries'])}")
    print(f"protocol_native_hits={len(native['watch_protocol_namespace_hits'])}")


if __name__ == "__main__":
    main()
