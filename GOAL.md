# Garmin Compatibility Layer — GOAL

## Mission

Independently understand the watch-facing protocol used by the lawfully obtained Garmin Connect 5.29 package and implement a compatibility layer that can communicate with the owner's Garmin watch without requiring Garmin Connect at runtime.

The project is protocol-reimplementation work, not an attempt to clone Garmin Connect. The finish line is a documented, tested, independently written protocol library plus a small reference client that exposes semantic operations suitable for an accessible application.

## Scope and boundaries

- Work only from the APK/APKM already acquired for this project, behavior observed on the owner's devices, Android/Bluetooth standards, and tool documentation.
- Do not redistribute Garmin binaries, source-like decompiler output, signing material, artwork, text, or other copyrighted application assets.
- Do not copy decompiled methods into the implementation. Decompiled code is evidence used to infer protocol behavior; implementation code must be independently written from the protocol specification in `spec/PROTOCOL.md`.
- Protocol facts such as UUIDs, message IDs, field layouts, state transitions, checksums, and observed byte sequences may be documented as interoperability facts.
- Do not extract or reuse Garmin account credentials, cloud tokens, private user data, or keys unrelated to the watch pairing/session being tested.
- Dynamic tests are performed only against devices/accounts the owner is authorized to use.
- Existing third-party Garmin protocol implementations are intentionally excluded as design inputs until the independent reconstruction reaches its completion gate. They may be used afterward only for cross-validation, with any discrepancy independently re-tested.

This is a technical project discipline, not a legal opinion.

## The no-cheating rule

Every implemented protocol unit follows this chain:

`evidence -> written protocol fact -> independent implementation -> test -> device verification`

A unit is **not complete** because a decompiler produced readable code. It is complete only when the behavior can be explained in our own protocol documentation, encoded/decoded by our implementation, tested against fixtures, and verified on the watch when hardware interaction is required.

For each protocol unit, record:

1. Evidence source: APK class/method location, manifest/resource location, native symbol, and/or dynamic capture identifier.
2. Preconditions and state: disconnected, bonded, paired, authenticated session, transfer state, etc.
3. Direction and transport: client->watch or watch->client, GATT service/characteristic, notification/write/read, MTU assumptions.
4. Frame layout: byte offsets, lengths, endianness, flags, message IDs, sequence numbers, integrity fields, and unknown bytes.
5. Semantics: what request/response/event means and which fields are confirmed versus hypothesized.
6. Failure behavior: malformed frame, timeout, wrong state, reconnect, duplicate packet, partial transfer.
7. Independent encoder/decoder implementation.
8. Automated tests using sanitized fixtures.
9. On-device verification where applicable.
10. `spec/PROTOCOL.md` update with evidence links.

A hypothesis is labeled `HYPOTHESIS` until supported by at least two independent observations or one observation plus an unambiguous static-code path. Unknown fields stay `UNKNOWN`; they are never silently guessed.

## Repository structure

```text
.
├── GOAL.md                       # this contract and completion gate
├── pyproject.toml                # uv/Python helper project
├── scripts/                      # repeatable analysis/checking helpers
├── src/garmin_proto_lab/         # independently written compatibility code
├── tests/                        # unit/property/integration tests
├── spec/
│   └── PROTOCOL.md               # protocol facts only, no copied Garmin code
├── analysis/
│   ├── bundle/                   # unpacked APKM
│   ├── jadx/                     # JADX output; evidence only
│   ├── apktool/                  # apktool output; evidence only
│   └── inventory/                # generated indexes/reports
├── evidence/
│   ├── static/                   # static-analysis notes and references
│   ├── dynamic/                  # sanitized trace findings
│   └── logs/                     # reproducible command logs
└── captures/                     # local captures; keep sensitive material out of git
```

Generated/decompiled material is evidence and must never be imported by the runtime library.

## Completion policy

The mandatory checklist below is the project finish line. `scripts/check_goal.py` must return success only when every mandatory checkbox is checked. A checkbox may be marked `[x]` only when its stated evidence and test artifact exist.

No requirement may be deleted merely because it is difficult. If the project scope must change, amend this file explicitly and record the reason in a dated decision note before changing the checklist.

<!-- REQUIRED-CHECKLIST:START -->

### M0 — Provenance and reproducibility

- [x] **REQ-001 Original artifacts recorded.** Preserve the APKM and base APK hashes and record file sizes, package name, version, version code, min/target SDK, and extraction date in `analysis/inventory/artifacts.json`.
- [x] **REQ-002 Bundle inventory reproducible.** A script can unpack/list the APKM and produce a deterministic inventory of all split APKs, architectures, DPIs, languages, native libraries, DEX files, and signing-certificate fingerprints.
- [x] **REQ-003 Toolchain recorded.** Record exact versions and acquisition method for Python/uv, Java, JADX, apktool, Android platform tools used, Wireshark/tshark if used, and any native reversing tool.
- [x] **REQ-004 Sensitive outputs isolated.** `.gitignore` excludes decompiler output, captures, credentials/session material, private device identifiers, and generated binaries unless explicitly sanitized.

### M1 — Static map of the application

- [x] **REQ-010 Base APK decompiled two ways.** Produce JADX output and apktool/smali output from the same hashed base APK, with successful-run logs.
- [x] **REQ-011 Manifest map complete.** Document Bluetooth, nearby-device, location, network, foreground-service, storage, and notification permissions plus BLE-related services/receivers/providers.
- [x] **REQ-012 BLE call-site index complete.** Index all direct and wrapped uses of Android BLE/GATT APIs, scanner APIs, characteristic reads/writes/notifications, MTU changes, bonding/pairing calls, and connection callbacks.
- [x] **REQ-013 UUID inventory complete.** Extract literal and constructed UUIDs and classify each as standard Bluetooth, Garmin-specific, unrelated, or unresolved.
- [x] **REQ-014 Serialization/framing candidates mapped.** Identify protocol codec classes, packet/frame builders/parsers, checksums/CRC, compression, protobuf/CBOR/JSON/custom binary use, and sequence/fragmentation logic.
- [x] **REQ-015 Crypto/session candidates mapped.** Identify watch-session authentication, key derivation/storage, nonce/counter handling, and integrity/encryption call paths without recording unrelated account secrets.
- [x] **REQ-016 Native-code boundary mapped.** Inventory `.so` libraries and determine whether any watch-facing protocol behavior crosses JNI/native code; if so, document the relevant exports/call sites.
- [x] **REQ-017 Static dependency graph written.** Produce a concise map from Android BLE callbacks -> Garmin transport -> frame codec -> message dispatcher -> feature handlers, with evidence references.

### M2 — Controlled dynamic baseline

- [ ] **REQ-020 Test device identity recorded.** Record watch model, firmware version, Android device/OS version, Garmin Connect version, and whether the watch began bonded/paired; redact unique identifiers from committed evidence.
- [ ] **REQ-021 Baseline service discovery captured.** Capture a clean connect and document discovered services, characteristics, descriptors, properties, MTU, notification subscriptions, and order of operations.
- [ ] **REQ-022 Clean pairing captured.** From a deliberately clean pairing state, record the complete app/watch pairing handshake and resulting persistent state while protecting secret values.
- [ ] **REQ-023 Reconnect captured.** Record app restart, Bluetooth toggle, watch reboot, and Android reboot reconnection behavior so persistent versus ephemeral session state is known.
- [ ] **REQ-024 Trace-to-code correlation complete.** Each GATT operation in the baseline handshake is linked to the corresponding static call path or explicitly marked unresolved with a follow-up requirement.

### M3 — Transport layer independently recreated

- [ ] **REQ-030 Discovery implemented.** Independent code discovers/selects the intended watch without depending on Garmin Connect.
- [ ] **REQ-031 Connection lifecycle implemented.** Connect, service discovery, characteristic selection, notification subscription, MTU negotiation, graceful disconnect, timeout, and reconnect are handled.
- [ ] **REQ-032 Raw transport verified.** Known writes and notifications can be reproduced/captured byte-for-byte at the GATT boundary without Garmin Connect running.
- [ ] **REQ-033 Fragmentation/reassembly implemented.** Payloads larger than one ATT write/notification round-trip correctly, including ordering, duplicate, missing, and partial-fragment behavior.
- [ ] **REQ-034 Transport tests pass.** Unit/property tests cover fragmentation, reassembly, length limits, invalid input, timeout, and reconnect state.

### M4 — Pairing and session layer independently recreated

- [ ] **REQ-040 Pairing state machine specified.** `spec/PROTOCOL.md` defines each pairing/session state, transition, message, timeout, persistence rule, and confirmed cryptographic primitive if present.
- [ ] **REQ-041 Fresh pairing works.** The compatibility client pairs with the test watch from a clean state with Garmin Connect absent/disabled for the test.
- [ ] **REQ-042 Persistent reconnect works.** After client restart and watch/phone reconnect scenarios, the client restores the expected session without unnecessarily re-pairing.
- [ ] **REQ-043 Wrong-state/error handling works.** Invalid session, stale state, rejected pairing, bad integrity, timeout, and user-cancel paths fail safely and recover predictably.
- [ ] **REQ-044 Session tests pass.** Sanitized fixtures and on-device integration tests cover fresh pair, normal reconnect, forced reset, and failure recovery.

### M5 — Frame and message codec independently recreated

- [ ] **REQ-050 Frame grammar specified.** Header/trailer fields, lengths, endianness, flags, channel/message IDs, sequence numbers, checksums/MACs, and fragmentation fields are documented.
- [ ] **REQ-051 Encoder/decoder round-trip passes.** Every sanitized captured frame used by the project decodes and re-encodes to the expected bytes except fields explicitly documented as nondeterministic.
- [ ] **REQ-052 Integrity logic verified.** Checksums/CRC/MAC behavior is reproduced with positive and negative test vectors.
- [ ] **REQ-053 Dispatcher implemented.** Message-family dispatch is data-driven or clearly structured and preserves unknown messages without crashing or silently discarding evidence.
- [ ] **REQ-054 Codec robustness passes.** Truncated, oversized, malformed, duplicated, reordered, and unknown frames are covered by tests.

### M6 — Protocol families reconstructed one by one

- [ ] **REQ-060 Message-family census complete.** Every watch-facing message family observed in the required workflow matrix has a stable identifier and is classified as implemented, intentionally unsupported with written rationale, or proven unrelated to the watch protocol. No family remains silently unclassified.
- [ ] **REQ-061 Device identity/capabilities implemented.** Read and parse model/product/firmware/capability information used by the required workflows.
- [ ] **REQ-062 Battery/status implemented.** Read/receive the watch battery and basic connection/status state used by the required workflows.
- [ ] **REQ-063 Time synchronization implemented.** Understand and reproduce time/timezone synchronization, including encoding and acknowledgement/error behavior.
- [ ] **REQ-064 Notification transport implemented.** Reproduce the watch-facing notification path needed by the reference client, including capability negotiation and dismissal/action behavior when supported by the test watch.
- [ ] **REQ-065 Activity/health transfer implemented.** Reproduce the transport/session semantics needed to enumerate and transfer at least one activity/health-data object end-to-end, preserving unknown metadata rather than fabricating it.
- [ ] **REQ-066 Large-object transfer implemented.** Resume/retry/checkpoint/integrity behavior for a transfer spanning many frames is documented and tested if used by activity/health sync.
- [ ] **REQ-067 Settings/capability query implemented.** The compatibility layer can query the watch-facing settings/capability data required to safely choose supported operations.
- [ ] **REQ-068 Required-workflow unknowns resolved.** All bytes/flags that influence branching, lengths, authentication, integrity, routing, or user-visible semantics in required workflows are understood. Cosmetic/reserved bytes may remain `UNKNOWN` only with evidence that varying them is unnecessary and unsafe to guess.

### M7 — Compatibility library and reference client

- [ ] **REQ-070 No Garmin runtime dependency.** The implementation starts and completes all required workflows with Garmin Connect stopped/uninstalled for the test; it does not import, invoke, patch, or load Garmin application code/assets.
- [ ] **REQ-071 Stable semantic API exists.** The library exposes semantic operations (connect/pair/status/time/notification/activity transfer) rather than requiring application code to construct raw Garmin frames.
- [ ] **REQ-072 Reference CLI exists.** A small CLI can run the required workflow matrix and emit structured, redacted logs useful for accessibility-app development.
- [ ] **REQ-073 Accessibility-friendly event model exists.** Status/progress/errors are surfaced as concise semantic events with deterministic states so a screen-reader-friendly UI does not need to interpret raw packet traffic.
- [ ] **REQ-074 Documentation is sufficient.** A developer who has not read the decompiled APK can implement a second client from `spec/PROTOCOL.md` and the public semantic API documentation alone.

### M8 — Verification and finish line

- [ ] **REQ-080 Unit suite passes.** All codec, state-machine, parsing, serialization, invalid-input, and persistence tests pass from a clean `uv` environment.
- [ ] **REQ-081 Property/fuzz checks pass.** Parsers have bounded-input tests and property/fuzz coverage for lengths, fragmentation, round-trips, and malformed data sufficient to catch obvious parser/state bugs.
- [ ] **REQ-082 Fresh-install integration passes.** On the designated watch/firmware, a clean compatibility-client install can discover, pair, reconnect, query identity/status/battery, sync time, deliver a notification, and transfer one representative activity/health object without Garmin Connect running.
- [ ] **REQ-083 Recovery matrix passes.** Tests pass after Bluetooth toggle, client restart, watch reboot, phone reboot, out-of-range reconnect, interrupted large transfer, and deliberate protocol timeout.
- [ ] **REQ-084 Evidence audit passes.** Every mandatory requirement links to evidence and/or an automated test; no completion box is justified solely by a decompiler screenshot or intuition.
- [ ] **REQ-085 Independence audit passes.** Runtime source contains no copied Garmin decompiler output, proprietary assets, embedded Garmin binaries, or dependency on an existing third-party Garmin protocol implementation.
- [ ] **REQ-086 Goal checker passes.** `uv run python scripts/check_goal.py GOAL.md` reports zero unchecked mandatory requirements.

<!-- REQUIRED-CHECKLIST:END -->

## Required workflow matrix

The integration finish line uses these workflows on the designated watch/firmware:

| Workflow | Clean state | Expected result |
|---|---|---|
| Discover | Bluetooth on, client unpaired | Correct watch can be selected without Garmin Connect |
| Fresh pair | Watch/client pairing state reset | Pairing succeeds and persistent state is stored |
| Reconnect | Previously paired | Reconnect succeeds without unnecessary re-pairing |
| Device info | Connected | Model/firmware/capabilities parsed semantically |
| Battery/status | Connected | Battery/status exposed semantically |
| Time sync | Connected | Watch accepts time/timezone update and response is verified |
| Notification | Connected | Representative notification reaches watch and lifecycle is observed |
| Activity/health read | Connected with one known record | At least one representative object transfers and is parsed/documented |
| Large transfer interruption | Transfer in progress | Resume/retry behavior is correct and data integrity is checked |
| Recovery | Restart/toggle/out-of-range scenarios | State recovers or fails with a documented, actionable error |

A future app may add more features, but these workflows define the protocol-compatibility v1 finish line. Any newly discovered protocol dependency required by them becomes mandatory and must be added to the checklist before v1 may be declared complete.

## Evidence conventions

Use stable identifiers:

- Static evidence: `S-####`
- Dynamic capture/observation: `D-####`
- Protocol fact: `P-####`
- Automated test: `T-####`
- Decision: `DEC-####`

Each `P-####` entry in `spec/PROTOCOL.md` should cite at least one `S-####` or `D-####`. Tests should cite the protocol fact they enforce.

Do not commit raw secrets or personally identifying watch/account data. When a real frame contains a secret/session token, preserve only the structure needed to understand the protocol and store the raw capture locally under `captures/`.

## Protocol fact confidence

Use one of these labels:

- `CONFIRMED`: reproduced independently and verified by test/device behavior.
- `SUPPORTED`: evidence strongly supports the interpretation but one independent verification step remains.
- `HYPOTHESIS`: plausible interpretation requiring targeted experiment.
- `UNKNOWN`: observed bytes/behavior with no asserted interpretation.

Only `CONFIRMED` facts may be treated as implementation requirements without a runtime fallback for unexpected values.

## Working order

Work layer-by-layer and do not jump ahead to feature semantics before lower layers are testable:

1. Provenance and toolchain.
2. Static BLE/protocol map.
3. One clean dynamic baseline.
4. GATT transport.
5. Pairing/session.
6. Frame grammar and codec.
7. One message family at a time.
8. Reference client and semantic API.
9. Recovery, robustness, and accessibility integration.
10. Completion/evidence audit.

When a higher-level feature reveals a lower-level unknown, stop, add a targeted experiment, resolve that dependency, then resume.

## Decision log

### DEC-0001 — Independent reconstruction discipline

We will not use an existing Garmin protocol library/specification as an implementation shortcut. Static inspection of Garmin's own lawfully obtained application and dynamic observation of the owner's watch are evidence sources; implementation is written from our own protocol document and validated independently.

### DEC-0002 — Python/uv for analysis and reference implementation

Use a small `uv` project for repeatable inventory scripts, capture parsers, codec experiments, tests, and the first reference compatibility client. System/decompiler tools remain external and their exact versions are recorded by REQ-003.

### DEC-0003 — Completion cannot be declared informally

The project is complete only when every checkbox between `REQUIRED-CHECKLIST:START` and `REQUIRED-CHECKLIST:END` is checked and the automated goal checker succeeds. Progress notes or partial feature demos do not override this gate.
