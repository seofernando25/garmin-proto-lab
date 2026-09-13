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

## Offline implementation gates completed without target hardware

These gates require implementation/specification/tests but do not themselves claim target-watch interoperability. Hardware-dependent neighboring requirements remain unchecked.

| Requirement | Status | Evidence / test |
|---|---|---|
| REQ-031 | complete | `transport.py`, `bleak_backend.py`; bounded lifecycle/reconnect tests in `tests/test_transport.py` |
| REQ-033 | complete | ATT byte-stream slicing plus COBS/protobuf/file reassembly; `test_ble_features.py`, `test_codec_dispatcher.py`, `test_protobuf_transport.py` |
| REQ-034 | complete | transport failure, MTU, reconnect, fragmentation and malformed-input test coverage |
| REQ-040 | complete | `spec/PROTOCOL.md` P-0201..P-0212 and `session.py` state/persistence model |
| REQ-043 | complete | wrong-state, rejected reconnect, confirm/integrity failure, timeout, reset and explicit user-cancel tests |
| REQ-050 | complete | `spec/PROTOCOL.md` P-0100..P-0115/P-0210..P-0211/P-0600 |
| REQ-052 | complete | CRC, XXTEA/session integrity, counter/IV, file CRC and protobuf status positive/negative tests |
| REQ-053 | complete | `dispatcher.py`, `link.py`, unknown-message preservation tests |
| REQ-054 | complete | malformed/truncated/oversized/duplicate/reordered/property tests in codec/fuzz suites |
| REQ-061 | complete | 5024 identity parser + Configuration + Smart/Core/GNCS capability codecs and semantic client exchange |
| REQ-062 | complete | 5023 parse/send plus semantic battery event tests |
| REQ-063 | complete | 5026/5030/5052 timezone/DST paths and acknowledgement/request-mode tests |
| REQ-067 | complete | legacy Configuration and flag-95 Smart feature-capability query implementation |
| REQ-071 | complete | `GarminClient` semantic connect/status/time/notification/file API |
| REQ-072 | complete | `garmin-proto` decode/encode/scan/services/probe/workflow CLI |
| REQ-073 | complete | deterministic `SemanticKind` / `SemanticEvent.accessible_text()` event model |
| REQ-080 | complete | 149 tests passed from a newly created `/tmp` uv environment on 2026-09-13 |
| REQ-081 | complete | Hypothesis property/bounded parser tests plus arbitrary BLE fragmentation/malformed-input coverage |
| REQ-085 | complete | `scripts/audit_independence.py`: decompiler/runtime boundary PASS; no Garmin code/assets/protocol dependency in runtime |

These completions do **not** satisfy REQ-032, REQ-041/042/044, REQ-051, REQ-060, REQ-064-066/068, REQ-070, REQ-074, or the target-hardware M8 integration/audit gates.
