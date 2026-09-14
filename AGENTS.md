# AGENTS.md

## Purpose

This repository implements an open-source compatibility layer for Garmin watches, with the required workflow centered on discovery, pairing/reconnect, watch-facing protocol transport, direct fitness-file extraction, and local FIT decoding without Garmin Connect at runtime.

`GOAL.md` is the completion contract. Do not weaken, delete, or silently reinterpret a requirement to make the project appear complete. If implementation work reveals a new requirement needed for the stated workflow, add it explicitly.

## Start every session here

1. Run `git status --short` and do not overwrite unrelated user work.
2. Read the unchecked items in `GOAL.md`.
3. Read the relevant section of `spec/PROTOCOL.md` before changing protocol behavior.
4. Run the focused tests for the code you touch, then the full offline gate before committing.

Useful commands:

```bash
uv sync --group dev
uv run pytest -q
python scripts/check_offline_completion.py
uv run python scripts/check_goal.py GOAL.md
```

For BLE hardware work:

```bash
uv sync --extra hardware
uv run garmin-proto scan --seconds 8
uv run garmin-proto services <address>
uv run garmin-proto probe <address>
```

## Sources of truth

Use these in this order:

- `GOAL.md` — mandatory acceptance requirements and finish line.
- `spec/PROTOCOL.md` — protocol facts, state machines, layouts, and test map.
- `docs/HARDWARE.md` — physical-watch verification procedure.
- `docs/INVESTIGATION.md` — concise research findings and current boundary.
- `docs/API.md` — application-facing semantics.
- `docs/DECISIONS.md` — research-source and licensing decisions.
- `docs/TOOLING.md` — analysis tools and reproducibility notes.
- `docs/RESEARCH_METHOD.md` — reverse-engineering and iteration procedure.
- `docs/HARDWARE_RESTEER_LOOP.md` — hardware-session observe/classify/resteer loop.
- `evidence/static/STATIC_MAP.md` — detailed static-analysis evidence.

Decompiler output under `analysis/`, APK/APKM files, native binaries, and raw captures are local research inputs, not public-repository artifacts.

## Repository map

- `src/garmin_proto_lab/transport.py`, `bleak_backend.py` — BLE/GATT lifecycle and physical routing.
- `frame.py`, `link.py`, `cobs.py`, `crc.py` — GFDI wire framing and transactions.
- `session.py`, `auth_protocol.py`, `auth_messages.py`, `crypto.py` — pairing, authentication, persistence, and secure sessions.
- `protobuf_*`, `smart_proto.py`, `file_access_*` — Smart protobuf and FileAccess control plane.
- `multilink.py`, `multilink_client.py`, `multilink_gfdi.py`, `mlr.py` — MultiLink and reliable transport.
- `filetransfer.py`, `file_client.py`, `fitness_sync.py` — legacy and next-generation file transfer/sync.
- `fit.py` — FIT validation, generic record preservation, and semantic fitness projections.
- `cli.py` — reference workflows and diagnostic commands.
- `tests/` — protocol/unit/property tests; new protocol behavior must be covered here.

## Protocol implementation rules

Protocol changes must follow this chain:

`evidence -> documented protocol fact -> implementation -> automated test -> watch verification when hardware-dependent`

Do not guess values that control routing, lengths, authentication, integrity, state transitions, or user-visible semantics. Preserve unknown numeric values and raw bytes where possible. A semantic decoder should be additive: keep the generic/raw representation even when a field receives a friendly name.

When a public implementation or SDK informs behavior, record the project, revision, and license in `docs/DECISIONS.md` or the investigation record. Copy code only when its license is compatible and attribution obligations are satisfied. Prefer recording protocol facts and implementing them in this project's data model.

Keep watch-facing behavior separate from cloud/account behavior. This project's required workflow must not depend on Garmin account credentials, cloud tokens, Garmin application code, or Garmin proprietary runtime libraries.

## Pairing and transport invariants

Preserve both pairing routes unless hardware evidence disproves them:

- normal fresh pairing: OS Bluetooth bond, then bonded GFDI/StubAuth behavior;
- explicit unbonded route: Garmin 5101–5111 authentication with persisted LTK/EDIV/RAND and 5102 reconnect.

Direct GFDI is preferred when the watch exposes it. Otherwise GFDI may run over MultiLink logical service 1. FileAccess is selected by peer Configuration bit 90; bit 95 gates the optional feature-capability exchange and is not the FileAccess enable bit.

MLR changes must retain sequence wrap, cumulative ACK behavior, retransmission/backoff, out-of-order suppression, and large-transfer tests.

## FIT/data rules

Never publish a downloaded FIT object as complete until its container integrity checks pass. Preserve File ID classification, header validation, and trailing FIT CRC validation. Use FileAccess checksum verification when the peer advertises it.

Keep generic FIT records and raw fields available alongside semantic projections. New fitness semantics should include tests for field number, scale/offset, invalid sentinel handling, arrays, timestamp behavior, and JSON serialization where applicable.

The fitness-sync workflow must remain resumable and crash-safe: private `.part` file, clear-byte offset resume, final validation, then atomic publish.

## Hardware evidence

Do not mark hardware requirements complete using static analysis or simulation alone. Follow `docs/HARDWARE.md` for the verification matrix and `docs/HARDWARE_RESTEER_LOOP.md` for the experiment/resteering behavior loop. Capture one controlled operation at a time.

Raw Bluetooth captures, bug reports, MAC addresses, serials, account IDs, pairing keys, session keys, and other private identifiers stay out of git. Store raw material only in ignored local paths such as `captures/`. Commit sanitized observations as `D-####` evidence and link them to the matching protocol/static facts.

The next physical-watch session should prioritize: service discovery and MTU/subscription order; clean system-bond pairing; separate `--garmin-auth` pairing; reconnect after process/Bluetooth/watch/host restart; real FileAccess listing/download; activity + monitoring + sleep/HRV/skin-temperature files where present; interrupted-transfer resume; and a final run with Garmin Connect stopped or absent.

## Code and test style

Target Python 3.11+. Prefer the standard library and existing project abstractions over new dependencies. Use type hints, small dataclasses/value objects, explicit enums, bounded async timeouts, and deterministic error paths. Avoid broad exception swallowing except during best-effort cleanup; protocol failures should remain observable.

For every behavior change:

```bash
uv run pytest -q tests/<relevant_test>.py
uv run pytest -q
python scripts/check_offline_completion.py
```

If a protocol fact or public API changes, update the corresponding documentation and test-map entry in the same commit. Do not make documentation claim stronger support than the tests/evidence justify.

## Git/public-repo hygiene

The public repository is `seofernando25/garmin-proto-lab`. Keep commits focused and descriptive. Before committing, inspect `git diff --check` and `git status --short`.

Never commit APK/APKM files, decompiler trees, native Garmin libraries, browser/download artifacts, raw captures, credentials, pairing material, device identifiers, or generated private fitness data. If a new local artifact class appears, update `.gitignore` before proceeding.

## Current boundary

The offline pairing/fitness implementation currently passes `scripts/check_offline_completion.py`. The remaining mandatory `GOAL.md` items are primarily physical-watch evidence and end-to-end acceptance gates. Treat that as a checkpoint, not proof that every Garmin ecosystem feature or every device-specific protocol branch is complete.

When hardware reveals a discrepancy, fix the implementation/spec/tests first and only then mark the associated requirement complete.
