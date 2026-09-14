# Semantic API

The public API keeps BLE, GFDI framing, authentication, file transport and fitness-file handling as separate layers. Wire layouts live in `spec/PROTOCOL.md`.

## Construction

```python
backend = BleakBackend()
requires_bond = requires_system_bond(pairing_store, device_address)
transport = BleTransport(backend, require_bond=requires_bond)
link = GfdiMessageLink(transport)
auth = AuthProtocolEngine(link, device_address, pairing_store)
client = GarminClient(
    link,
    HostIdentity(1, "Independent Garmin Client", "Independent", "Python client"),
    frozenset({71, 90}),
    auth=auth,
)
```

Set `BleTransport(..., require_bond=True)` only when an OS-level Bluetooth bond is explicitly required. `FilePairingStore` persists LTK/EDIV/RAND for GFDI reconnect; device identifiers are hashed and the store is mode `0600`.

## GarminClient

| Operation | Method |
|---|---|
| BLE discovery | `discover(timeout)` |
| connect / disconnect | `connect(device)`, `disconnect()` |
| status | `status_snapshot()` |
| protobuf feature capabilities | `refresh_feature_capabilities()` |
| host battery update | `send_phone_battery(percent)` |
| time synchronization | `sync_time()` |
| notification source | `send_notification_source(source)` |
| notification data/attributes | `send_notification_data(payload)` |
| legacy file directory | `list_files()` / `list_activity_health_files()` |
| legacy file read | `read_file(index)` |
| validated legacy fitness read | `download_activity_health(remote)` |
| archive legacy file | `archive_file(index)` |

`client.events` is an `asyncio.Queue[SemanticEvent]`. Events expose parsed semantic state rather than raw packet hex.

## Pairing and reconnect

`AuthProtocolEngine` implements messages 5101–5112. Visible pairing obtains the decimal watch passkey through `passkey_provider(mode, timeout_seconds)`. Just-works uses the zero 16-byte passkey. OOB pairing accepts a 16-byte passkey. Persistent reconnect sends stored EDIV/RAND, derives a fresh session key, verifies it, then installs the secure wrapper after the plaintext 5111 acknowledgement is written.

Garmin Connect has two authentication routes. Its fresh pairing strategy requests the Android/BlueZ system bond. Once the remote is bonded, `DefaultAuthDelegate.isGarminAuthAllowed()` disables proprietary Garmin auth and the GFDI stack uses its stub auth handler, so no LTK/EDIV/RAND record is expected from that route. On Linux `BleakBackend` registers a temporary BlueZ `KeyboardDisplay` Agent1 during bonding.

The unbonded route runs Garmin messages 5101–5111 and stores LTK/EDIV/RAND for 5102 reconnect; a saved Garmin-auth record therefore skips a new system bond request. The CLI follows this split automatically. `--system-bond` forces the bonded route and `--garmin-auth` forces the unbonded Garmin-auth route for controlled testing.

## Feature capabilities

Legacy Configuration 5050 controls optional managers. Relevant recovered flags are:

- 6: GNCS notification provider
- 71: current-time request path
- 90: next-generation FileAccess manager
- 95: Smart/Core feature-capability exchange

Peer bit 90 is the runtime FileAccess/Sync2 selector. Peer bit 95 only enables the optional Core FeatureCapabilities exchange. When 95 is present, `refresh_feature_capabilities()` exchanges Core field 8/9 and stores optional FileAccess checksum/custom-flag metadata when advertised; FileAccess remains usable from bit 90 even when that extension is absent.

## FileAccess and MultiLink

`FileAccessControlClient` implements the recovered FileAccess schema: item-list pagination, pull/push negotiation, transfer status, priority updates, item deletion, flag modification, cancellation, item/resource notifications, software-update part-number requests and truncated-MD5 queries.

```python
control = client.file_access_control
ml = MultiLinkClient(
    backend,
    transport.services,
    connection_id=0x01,  # Garmin Connect client_uuid / MultiLink client identity
    max_write_length=transport.write_payload_size,
)
await ml.initialize()
listing = await control.list_items(requested_modified_time=True)
downloader = FileAccessMlrDownloader(control, ml)
download = await downloader.download(listing.items[0], verify_checksum=True)
```

`FileAccessMlrDownloader` uses the recovered MLR sender/receiver state: a 64-value sequence space, 32-packet initial window, cumulative ACKs, five-packet immediate ACK threshold, 10 ms deferred ACK, RTT-derived RTO, timeout backoff and retransmission. Pulls support byte-offset resume, optional zlib compression, Transfer Status completion and FileAccess cancellation on failure.

`GetItemChecksum` uses the first eight MD5 digest bytes interpreted little-endian as protobuf fixed64. The downloader can compare that value after a complete pull.

## Persistent fitness synchronization

`NextGenFitnessSync` turns FileAccess into a local fitness archive:

```python
sync = NextGenFitnessSync(
    control,
    downloader,
    "~/Garmin-FIT",
    capabilities=client.file_access_capabilities,
)
results = await sync.sync()
```

It filters recovered fitness data types (`FIT_TYPE_4`, `FIT_TYPE_32`, `FIT_TYPE_49` and the other modeled health FIT types), writes mode-`0600` `.part` files as bytes arrive, resumes from the saved prefix, performs server truncated-MD5 verification when advertised, validates the FIT File ID and full-file CRC, then atomically publishes the `.fit` file. Completed valid files are not downloaded again.

## CLI

```bash
uv run garmin-proto scan --seconds 8
uv run garmin-proto services <address>
uv run garmin-proto probe <address>
uv run garmin-proto reset-pairing <address>
uv run garmin-proto pair <address>
uv run garmin-proto fitness-sync <address> --output ~/Garmin-FIT --fit-json
uv run garmin-proto workflow <address> --download-first-activity
```

`fitness-sync` selects FileAccess when the watch advertises it and otherwise uses the legacy GFDI directory/download path. `--fit-json` writes mode-`0600` JSON sidecars containing the decoded generic FIT records plus decoded standard activity samples. Add `--legacy` to force the legacy path, `--compression` to request compressed transfer, or `--system-bond` to force a new OS Bluetooth bond on a saved GFDI pairing record. Pairing secrets and session keys are not printed.

## GFDI physical route selection

`BleTransport` first uses a discovered dedicated GFDI characteristic pair. If none exists and the Garmin MultiLink service is present, it registers logical MultiLink service ID 1 and carries the identical GFDI COBS byte stream over that handle. Reliable service handles use the MLR ARQ engine; non-reliable handles use one-byte MultiLink framing. Higher layers do not need separate code paths.

## FIT fitness data

`parse_fit_records(data)` returns every FIT data record with numeric global-message and field identifiers preserved. `extract_activity_samples(data)` projects standard global-message-20 Record fields into `ActivitySample` values with UTC timestamp, latitude/longitude, altitude, heart rate, cadence, distance, speed, power, and temperature. `extract_activity_summaries(data)` projects Session/Lap/Activity aggregates (timer, distance, calories, speed, heart rate, cadence, power, ascent/descent, training effect, sport/sub-sport). `extract_wellness_samples(data)` projects the standard weight/body-composition, blood-pressure, monitoring, HRV, resting-HR, stress, SpO2, sleep-level, respiration-rate, and Body Battery messages using FIT Profile 21.214 field scales and enums. The FIT header and full-file CRC are validated before records are exposed, and the generic numeric/raw representation remains available beside semantic fields.
