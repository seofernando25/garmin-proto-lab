# garmin-proto-lab

Open-source interoperability implementation of Garmin's watch-facing GFDI protocol, focused on pairing and direct fitness-data extraction without Garmin Connect.

**Status:** static reconstruction and offline implementation cover the protocol paths required for pairing, reconnect, legacy file transfer, next-generation FileAccess, MultiLink/MLR, and FIT validation. The remaining acceptance gate is execution against a physical Garmin watch.

## Implemented

- BLE discovery, direct GFDI selection, automatic GFDI-over-MultiLink service-1 fallback, ATT write chunking, reconnect handling
- GFDI COBS framing, CRC-16, transactions, configuration and device information
- Garmin authentication 5101–5112: visible/just-works/OOB passkeys, STK/LTK, persistent reconnect, session keys and secure wrapping
- dual pairing modes: system-bonded BLE pairing (Garmin proprietary auth disabled) and unbonded Garmin 5101–5111 authentication with persistent LTK/EDIV/RAND reconnect
- legacy directory discovery and activity/health FIT downloads
- Smart protobuf 5043/5044/5045 and FileAccess service fields 1–26 used by the recovered schema
- MultiLink registration and the MLR reliable transport: 64-value sequence space, cumulative ACKs, 32→63 send window, 10 ms deferred ACK, five-packet ACK threshold, RTT/RTO estimation and retransmission backoff
- FileAccess pagination, pull/resume, transfer status, cancellation, truncated-MD5 verification, zlib transfer, file-change notifications and control operations
- activity/monitoring/sleep/metrics/HRV/skin-temperature classification, FIT header/File ID/full-file CRC validation, and activity/wellness decoding
- persistent `fitness-sync` workflow with resumable `.part` files and private file permissions

## Quick start

```bash
uv sync --group dev
uv run pytest -q
python scripts/check_offline_completion.py
uv run garmin-proto --help
```

BLE support:

```bash
uv sync --extra hardware
uv run garmin-proto scan --seconds 8
uv run garmin-proto reset-pairing <address>   # for a controlled fresh-pair test
uv run garmin-proto pair <address>
```

On Linux, the hardware backend registers a temporary BlueZ `KeyboardDisplay` Agent1 during the system-bond operation, so a headless terminal can enter/confirm the Bluetooth passkey. `reset-pairing` removes both the OS bond and stored Garmin LTK/EDIV/RAND by default.

Direct fitness extraction:

```bash
uv run garmin-proto fitness-sync <address> --output ~/Garmin-FIT
# Optional JSON sidecars with generic FIT records plus decoded standard activity summaries/samples and wellness samples:
uv run garmin-proto fitness-sync <address> --output ~/Garmin-FIT --fit-json
```

`fitness-sync` uses next-generation FileAccess when the watch advertises it and otherwise uses the legacy GFDI file path. Completed FIT files are validated before publication; interrupted FileAccess pulls retain a private `.part` file and resume from its byte length on the next run.

Useful protocol tools:

```bash
uv run garmin-proto decode <hex-wire-bytes>
uv run garmin-proto encode 5023 <payload-hex>
uv run garmin-proto time-payloads
```

## Documentation

`spec/PROTOCOL.md` is the protocol specification. `docs/INVESTIGATION.md` records the reverse-engineering findings, `docs/API.md` describes the semantic API, `docs/HARDWARE.md` is the watch verification runbook, and `GOAL.md` is the completion contract.

## Remaining gate

No Garmin watch is currently attached to the development machine. The unchecked completion items therefore require hardware: live GATT discovery, first-time pairing, persistent reconnect, representative activity/monitoring/sleep downloads, interruption/recovery, and final execution with Garmin Connect stopped or absent.
