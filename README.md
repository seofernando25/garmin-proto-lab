# garmin-proto-lab

Independent interoperability research and a clean-room Python reference implementation for Garmin's watch-facing GFDI protocol.

**Status:** the offline protocol stack is implemented and tested; target-watch verification is still required. This repository does not contain Garmin APKs, decompiled Garmin source, proprietary assets, account credentials, or a dependency on an existing Garmin protocol implementation.

## What is implemented

- BLE transport abstraction plus a Bleak/BlueZ backend
- Garmin service/characteristic selection and ATT stream chunking
- COBS framing, GFDI frames, CRC-16, request/response correlation
- XXTEA authentication, pairing/session state, LTK reconnect, secure-session wrapping
- device information, configuration, battery, and time synchronization
- GNCS notification subscription/source/control-point/data-source codecs
- file directory discovery, file download, retry/integrity state, and basic FIT inspection
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
uv run garmin-proto scan --timeout 8
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

See `docs/INVESTIGATION.md` for the concise investigation record and `GOAL.md` for the non-negotiable completion gate.

## Current blocker

A target Garmin watch is not presently available at the development machine. The remaining decisive work is live GATT discovery, fresh pairing, reconnect persistence, end-to-end notification delivery, representative activity/health transfer, and recovery testing on the designated watch/firmware.
