#!/usr/bin/env python3
"""Create a deterministic inventory of the local Garmin APKM research input."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from androguard.core.apk import APK
from loguru import logger

logger.remove()

ROOT = Path(__file__).resolve().parents[1]
APKM = ROOT / "garmin-connect-5.29.apkm"
BASE_COPY = ROOT / "garmin-connect-5.29-base.apk"
BUNDLE = ROOT / "analysis" / "bundle"
OUT = ROOT / "analysis" / "inventory"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def cert_fingerprints(apk: APK) -> dict[str, Any]:
    schemes = {
        "v1": apk.is_signed_v1(),
        "v2": apk.is_signed_v2(),
        "v3": apk.is_signed_v3(),
        "v3.1": apk.is_signed_v31(),
    }
    cert_bytes: list[bytes] = []
    # get_certificates() aggregates supported schemes in Androguard.
    try:
        for cert in apk.get_certificates() or []:
            try:
                cert_bytes.append(cert.dump())
            except Exception:
                pass
    except Exception:
        pass
    # Explicit scheme methods ensure v2/v3-only APKs are represented.
    for method_name in (
        "get_certificates_der_v2",
        "get_certificates_der_v3",
        "get_certificates_der_v31",
    ):
        method = getattr(apk, method_name, None)
        if method is None:
            continue
        try:
            for der in method() or []:
                if isinstance(der, bytes):
                    cert_bytes.append(der)
        except Exception:
            pass
    unique = {hashlib.sha256(der).hexdigest() for der in cert_bytes}
    return {"schemes": schemes, "certificate_sha256": sorted(unique)}


def inspect_apk(path: Path) -> dict[str, Any]:
    apk = APK(str(path))
    files = sorted(apk.get_files())
    dex = [x for x in files if re.fullmatch(r"classes(?:\d+)?\.dex", x)]
    native = [x for x in files if x.startswith("lib/") and x.endswith(".so")]
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "package": apk.get_package(),
        "version_name": apk.get_androidversion_name(),
        "version_code": apk.get_androidversion_code(),
        "min_sdk": apk.get_min_sdk_version(),
        "target_sdk": apk.get_target_sdk_version(),
        "dex_files": dex,
        "native_libraries": native,
        "signing": cert_fingerprints(apk),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    info = json.loads((BUNDLE / "info.json").read_text(encoding="utf-8"))
    base = inspect_apk(BUNDLE / "base.apk")
    if sha256_file(BASE_COPY) != base["sha256"]:
        raise SystemExit("project base APK does not match APKM base.apk")

    artifacts = {
        "source": "APKMirror bundle already acquired for this project",
        "bundle_extraction_date_utc": "2026-09-13",
        "apkm": {
            "file": APKM.name,
            "bytes": APKM.stat().st_size,
            "sha256": sha256_file(APKM),
        },
        "base_apk": {
            "file": BASE_COPY.name,
            "bytes": BASE_COPY.stat().st_size,
            "sha256": sha256_file(BASE_COPY),
            "package": base["package"],
            "version_name": base["version_name"],
            "version_code": base["version_code"],
            "min_sdk": base["min_sdk"],
            "target_sdk": base["target_sdk"],
            "signing": base["signing"],
        },
        "apkm_metadata": {
            key: info.get(key)
            for key in (
                "apkm_version",
                "apk_title",
                "release_version",
                "variant",
                "versioncode",
                "pname",
                "post_date",
                "languages",
                "arches",
                "dpis",
                "min_api",
            )
        },
    }

    split_paths = sorted(BUNDLE.glob("*.apk"), key=lambda p: p.name)
    split_records = [inspect_apk(path) for path in split_paths]
    native_inventory = sorted(
        {
            lib
            for record in split_records
            for lib in record["native_libraries"]
        }
    )
    dex_inventory = sorted(
        {
            f"{record['file']}:{dex}"
            for record in split_records
            for dex in record["dex_files"]
        }
    )
    certificate_set = sorted(
        {
            fp
            for record in split_records
            for fp in record["signing"]["certificate_sha256"]
        }
    )
    bundle_inventory = {
        "package": info["pname"],
        "version_name": info["release_version"],
        "version_code": info["versioncode"],
        "architectures": sorted(info.get("arches", [])),
        "dpis": sorted(info.get("dpis", []), key=lambda x: int(x)),
        "languages": sorted(info.get("languages", [])),
        "apk_count": len(split_records),
        "apks": split_records,
        "dex_inventory": dex_inventory,
        "native_library_inventory": native_inventory,
        "certificate_sha256_set": certificate_set,
    }

    (OUT / "artifacts.json").write_text(
        json.dumps(artifacts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (OUT / "bundle_inventory.json").write_text(
        json.dumps(bundle_inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"package={base['package']}")
    print(f"version={base['version_name']} ({base['version_code']})")
    print(f"sdk=min{base['min_sdk']} target{base['target_sdk']}")
    print(f"apks={len(split_records)} dex_entries={len(dex_inventory)} native_libs={len(native_inventory)}")
    print(f"cert_sha256={','.join(certificate_set) if certificate_set else 'NONE'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
