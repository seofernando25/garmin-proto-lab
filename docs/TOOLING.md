# Tooling

This file records the practical tool stack used to analyze Garmin Connect and maintain the compatibility implementation. It is a reproducibility guide, not a requirement to use every tool in every session.

## Project/runtime tools

- **Python 3.11+** — implementation, one-off binary/protobuf inspection, fixture generation, and deterministic analysis scripts.
- **uv** — environment/dependency management and reproducible test execution.
- **pytest + Hypothesis** — unit, state-machine, binary-vector, failure-path, and property testing.
- **ripgrep/grep/find** — first-pass source and artifact searches. Prefer narrow package/path searches after the first broad hit.
- **git + GitHub CLI** — public history, source-reference checkout, focused commits, and push verification.

Useful baseline:

```bash
uv sync --group dev
uv run pytest -q
python scripts/check_offline_completion.py
```

## Android package analysis

### JADX

Project-local JADX is the primary readable Java/Kotlin reconstruction. Use it for class relationships, call paths, constants, state transitions, generated protobuf accessors, and high-level dataflow.

Typical approach:

```bash
tools/bin/jadx -d analysis/jadx analysis/bundle/base.apk
rg -n 'BluetoothGatt|requestMtu|createBond|writeCharacteristic' analysis/jadx/sources
rg -n '5002|5050|5101|5102|5111' analysis/jadx/sources
```

Do not treat a single decompiled method as authoritative when JADX reports bad-code/region errors. Resolve important failures with smali or a second representation.

### Apktool

Apktool provides the manifest/resources/smali view. It was used to verify Android permissions/components and to recover control flow where JADX failed.

```bash
tools/bin/apktool d analysis/bundle/base.apk -o analysis/apktool
```

Use smali especially for branch conditions, field widths, enum constants, and exception paths that are ambiguous in reconstructed Java.

### Androguard

Androguard is used by `scripts/inventory.py` for package metadata, SDK levels, DEX/native inventories, signatures, and reproducible APK provenance.

```bash
uv run python scripts/inventory.py
```

### Bundle extraction

The APKMirror `.apkm` is a ZIP containing `base.apk` and split APKs. `scripts/extract_bundle.py` and the inventory scripts establish exactly which inputs were analyzed. Never infer split behavior from `base.apk` alone when architecture/resource/native code could live in a split.

## Native-code analysis

The FileAccess data path crosses JNI into Garmin's reliable MultiLink implementation. Native investigation used:

- **readelf / objdump / strings** for quick library census, symbols, imports, constants, and architecture.
- **Ghidra / rizin** when managed-code call sites establish that a native function matters to the required workflow.
- Python scripts for converting native constants/test vectors into deterministic fixtures.

Start with the managed JNI boundary: identify the Java/Kotlin wrapper, native method names, input/output buffers, and state transitions before opening the native binary. This drastically reduces the search space.

For MLR, native self-test/formatter vectors were more valuable than attempting to understand every function. Extract observable invariants—header bits, sequence space, ACK semantics, windows, timers, retry behavior—then encode those as tests.

## Protobuf analysis

Garmin ships generated protobuf classes. They are often a better schema source than tracing every caller manually.

Procedure:

1. Find the generated class and its embedded descriptor/string data.
2. Record service/extension field numbers and nested message fields.
3. Trace only the fields used by the watch-facing handler.
4. Implement the minimal protobuf wire operations locally in `protobuf_wire.py`/related modules.
5. Add binary-vector tests for requests, responses, missing fields, and unknown enums.

Do not require generated Garmin classes at runtime.

## FIT/profile references

The project has its own FIT container/record parser. Standard field semantics were cross-checked with the public Garmin FIT Python SDK profile; Garmin-specific wellness/file semantics were also cross-checked against public interoperability projects such as Gadgetbridge. Record exact revision/license in `docs/DECISIONS.md` whenever a public source materially changes implementation behavior.

The key rule is additive decoding: semantic names/scales are useful, but raw numeric message/field IDs and bytes remain available.

## Bluetooth/hardware tools

When a watch is available:

- **btmon** — Linux-side HCI/GATT capture during compatibility-client tests.
- **Android Bluetooth HCI snoop + adb bugreport** — reference Garmin Connect captures when an Android reference run is needed.
- **Wireshark/tshark** — packet inspection and ATT/GATT correlation.
- **Bleak + BlueZ/dbus-fast** — actual compatibility-client BLE transport and pairing.

Keep raw captures ignored. Commit only sanitized, minimal evidence records.

## Project analysis scripts

- `scripts/toolchain.py` — records versions/provenance of the analysis toolchain.
- `scripts/inventory.py` — fingerprints APK/APKM inputs and split/native inventories.
- `scripts/static_map.py` — deterministic BLE/UUID/message/native-boundary indexes.
- `scripts/check_static_requirements.py` — validates static evidence prerequisites.
- `scripts/check_required_unknowns.py` — prevents required-workflow branch-controlling unknowns from silently surviving.
- `scripts/check_protocol_docs.py` — enforces important protocol/test documentation markers.
- `scripts/check_public_wording.py` — catches stale wording that misstates the project's current scope/policy.
- `scripts/check_offline_completion.py` — umbrella offline gate; run before public commits that alter protocol behavior.

## Installation policy

Prefer project-local/reproducible installs over mutating the host. If a required system package is absent, first determine whether the task can be completed with a project-local binary, Python dependency, or existing tool. Use elevated installation only when necessary and authorized. Never expose secrets from environment files in logs or commits.
