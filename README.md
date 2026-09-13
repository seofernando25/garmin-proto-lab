# garmin-proto-lab

Independent interoperability research and a clean-room Python reference implementation for Garmin's watch-facing GFDI protocol.

**Status:** the offline protocol stack is implemented and tested; target-watch verification is still required. This repository does not contain Garmin APKs, decompiled Garmin source, proprietary assets, account credentials, or a dependency on an existing Garmin protocol implementation.

## What is implemented

- BLE transport abstraction plus a Bleak/BlueZ backend
- Garmin service/characteristic selection and ATT stream chunking
- COBS framing, GFDI frames, CRC-16, request/response correlation
- XXTEA authentication, pairing/session state, LTK reconnect, secure-session wrapping
- device information, configuration, battery, time, and Smart protobuf feature capabilities
- GNCS notification subscription/source/control-point/data-source codecs
- legacy file directory/download plus basic FIT inspection
- next-gen FileAccess protobuf, MultiLink registration, clean-room MLR data path, cancel/recovery, and optional zlib pull (offline-tested)
- semantic client API and `garmin-proto` CLI

The test suite currently covers the reconstructed offline behavior. Passing tests do **not** imply compatibility with a particular watch until the hardware verification matrix in `GOAL.md` passes.

## Quick start

```bash
uv sync --group dev
uv run pytest -q
uv run garmin-proto --help
```

For BLE hardware commands:

```bash
uv sync --extra hardware
uv run garmin-proto scan --seconds 8
```

Useful offline tools:

```bash
uv run garmin-proto decode <hex-wire-bytes>
uv run garmin-proto encode 5023 <payload-hex>
uv run garmin-proto time-payloads
```

## Project discipline

The implementation follows:

`evidence -> protocol fact -> independent implementation -> automated test -> device verification`

Decompiled output is local evidence only. Runtime code is written from `spec/PROTOCOL.md`, not copied from Garmin code. Existing third-party Garmin protocol implementations are intentionally excluded as implementation inputs until the independent reconstruction is verified.

See `docs/INVESTIGATION.md` for the concise investigation record, `docs/API.md` for the semantic interface, `docs/HARDWARE.md` for the verification runbook, and `GOAL.md` for the non-negotiable completion gate.

## Current blocker

A target Garmin watch is not presently available at the development machine. Static reconstruction can continue, but the decisive gate is now hardware validation: live GATT discovery, fresh pairing/reconnect persistence, and representative legacy/next-gen activity or health downloads. The clean-room MultiLink/MLR path is intentionally conservative (uncompressed by default, immediate cumulative ACKs) until a real watch trace validates timing, service registration, compression, and recovery behavior.
