# Requirement Evidence Matrix

This file records why a `GOAL.md` checkbox may be checked. A checkbox is not changed merely because this table has an entry; the referenced artifacts/tests must exist and pass.

| Requirement | Status | Evidence / test |
|---|---|---|
| REQ-001 | complete | `analysis/inventory/artifacts.json`, `evidence/logs/inventory.txt`, S-0001 |
| REQ-002 | complete | `scripts/extract_bundle.py`, `analysis/inventory/bundle_inventory.json`, `evidence/logs/bundle-reproducibility.txt`, S-0001 |
| REQ-003 | complete | `analysis/inventory/toolchain.json`, `evidence/logs/toolchain.txt`, S-0002 |
| REQ-004 | complete | `.gitignore`, `scripts/check_static_requirements.py` |
| REQ-010 | complete | `evidence/logs/apktool-decode.log`, `evidence/logs/jadx-fallback-decode.log`, S-0002 |
| REQ-011 | complete | `analysis/inventory/manifest_map.json`, S-0003 |
| REQ-012 | complete | `analysis/inventory/ble_call_sites.json`, S-0004 |
| REQ-013 | complete | `analysis/inventory/uuid_inventory.json`, S-0005 |
| REQ-014 | complete | S-0006, S-0009, `spec/PROTOCOL.md` P-0100..P-0114/P-0400.. |
| REQ-015 | complete | S-0007, `spec/PROTOCOL.md` P-0200..P-0211 |
| REQ-016 | complete | `analysis/inventory/native_boundary.json`, S-0011 |
| REQ-017 | complete | S-0012 |

All M2 and later requirements remain pending unless explicitly added below after their stated dynamic/implementation evidence exists. The absence of the watch is not a reason to weaken those gates.
