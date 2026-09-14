# Reverse-engineering method

This is the procedural playbook used to turn a large Android package into a testable watch compatibility implementation. Future sessions should continue from documented facts rather than restarting broad decompilation.

## 1. Fix the input and provenance

Before interpreting behavior, fingerprint the exact APKM/base/splits and record tool versions. Run the inventory scripts and preserve hashes. A protocol observation is only useful if we know which build produced it.

Do not commit proprietary APKs, split APKs, `.so` libraries, decompiler output, or raw captures.

## 2. Start from the required workflow

The required product path is:

```text
discover watch
  -> establish BLE route
  -> pair/authenticate
  -> GFDI handshake/configuration
  -> choose legacy or FileAccess sync
  -> enumerate fitness objects
  -> transfer bytes reliably
  -> validate FIT object
  -> decode locally
```

Search outward from those boundaries. Avoid spending time on unrelated Garmin Connect cloud/UI features unless they are proven dependencies of this path.

## 3. Build a static map before deep reading

Use `scripts/static_map.py` plus targeted `rg` searches to establish:

- BLE service/characteristic UUID candidates;
- Android GATT connection lifecycle;
- message IDs and handler registration;
- configuration/capability bits;
- authentication handlers and persistence;
- protobuf extensions/services;
- file-transfer and MultiLink entry points;
- JNI/native boundaries.

This map tells us which classes deserve careful reading and prevents random browsing through tens of thousands of files.

## 4. Trace dataflow, not names

Obfuscated names are disposable. Follow values:

- where a field is parsed;
- whether it controls a branch;
- how many bytes are consumed;
- what is persisted;
- which response is produced;
- which downstream method reads it.

For each required field, answer: width, endianness, optionality, valid values, failure behavior, and whether it influences control flow.

A field that is copied but never inspected can remain reserved/raw. A field that influences routing, authentication, integrity, lengths, or state cannot remain guessed.

## 5. Resolve decompiler ambiguity with another view

For important methods with JADX warnings/errors:

1. inspect neighboring reconstructed methods and interfaces;
2. inspect generated protobuf descriptors if applicable;
3. read Apktool smali for exact branches/constants;
4. inspect native code only if the managed call path reaches JNI;
5. use public implementations/specs as cross-reference when useful.

Do not promote a fact into `spec/PROTOCOL.md` merely because one decompiler rendering looked plausible.

## 6. Split protocol facts from product policy

Examples:

- **Protocol fact:** Configuration bit 90 selects Sync2/FileAccess.
- **Product policy:** `fitness-sync` prefers FileAccess when bit 90 is present.

- **Protocol fact:** MultiLink service 1 can carry GFDI.
- **Product policy:** direct GFDI is preferred before falling back to MultiLink service 1.

Keeping these separate makes hardware corrections easier and avoids encoding Garmin Connect's unrelated application behavior as wire requirements.

## 7. Turn every useful finding into an executable invariant

Preferred progression:

```text
static/public evidence
  -> S-#### evidence note
  -> P-#### protocol statement
  -> implementation
  -> T-#### automated test
```

Good tests include:

- exact binary vectors;
- encode/decode round trips;
- malformed/truncated payloads;
- wrong-state transitions;
- timeout/retry paths;
- sequence wrap and out-of-order delivery;
- unknown-enum preservation;
- file integrity mismatch;
- interrupted/resumed transfer;
- JSON/semantic projection while retaining raw fields.

Use synthetic fixtures for structure and sanitized real fixtures after hardware capture.

## 8. Native-code workflow

Only enter native reversing once managed code proves the native path is required.

For each JNI boundary:

1. document the Java/native API and buffers;
2. identify the state object and lifecycle calls;
3. search native strings/constants/self-tests;
4. recover wire-observable behavior first;
5. model the smallest state machine that reproduces those observations;
6. test it heavily before expanding scope.

This method produced the MLR implementation: sequence/request numbers, cumulative ACKs, window growth, delayed ACK, RTT/RTO, timeout backoff, retransmission, and native formatter vectors were sufficient for the required transport without recreating an entire native library.

## 9. Public-reference workflow

Public open-source Garmin interoperability implementations and Garmin's public FIT SDK/profile may be used as research inputs. When they contribute a fact:

1. record repository/project, exact revision, and license;
2. compare against APK/native evidence where available;
3. document the resulting protocol fact in our own terminology;
4. write or update a test;
5. do not silently import incompatible code.

If sources disagree, leave the point unresolved until a stronger static path or hardware observation decides it.

## 10. Keep decoding lossless

For FIT and protocol payloads, retain unknown/raw information. Semantic projections should not replace the generic representation.

This is especially important for device-specific Garmin data. A new watch can introduce fields/messages that our semantic layer does not understand yet; the compatibility layer should still preserve and surface them numerically rather than discard the file.

## 11. Research iteration loop

For each unresolved requirement:

```text
choose one concrete question
  -> gather the smallest relevant evidence
  -> state the hypothesis explicitly
  -> implement only the implied behavior
  -> add focused tests
  -> run regression/offline gates
  -> update protocol + investigation docs
  -> commit a coherent unit of progress
```

Avoid building far ahead of the evidence. Prefer several evidence-backed increments.

## 12. Completion discipline

`GOAL.md` is the acceptance contract. Static reconstruction can satisfy static/implementation requirements, but hardware requirements stay unchecked until the designated watch proves them.

Before a protocol commit:

```bash
git diff --check
uv run pytest -q
python scripts/check_offline_completion.py
```

Before claiming the project complete, `scripts/check_goal.py GOAL.md` must report no unchecked mandatory requirements.
