# Semantic API

The public API intentionally hides raw GFDI frames from an accessibility-facing application. Protocol wire details live in `spec/PROTOCOL.md`.

## Construction

Compose the layers in this order:

```python
backend = BleakBackend()
transport = BleTransport(backend)
link = GfdiMessageLink(transport)
auth = AuthProtocolEngine(link, device_address, pairing_store)
client = GarminClient(
    link,
    HostIdentity(1, "Independent Garmin Client", "Independent", "Python client"),
    frozenset({6, 71}),
    auth=auth,
)
```

`FilePairingStore` persists only the pairing material needed for reconnect. Device identifiers are hashed in the store and the file is created mode `0600`.

## Main operations

`GarminClient` exposes the v1 semantic operations:

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
| file directory | `list_files()` / `list_activity_health_files()` |
| file read | `read_file(index)` |
| activity/health read + FIT cross-check | `download_activity_health(remote)` |
| archive downloaded file | `archive_file(index)` |

The client never requires application code to manually construct COBS, CRC, authentication, or file-transfer frames.

## Events

`client.events` is an `asyncio.Queue[SemanticEvent]`. Each event has a stable `kind`, parsed `value`, source `message_type`, and `accessible_text()` summary. Current kinds cover authentication, device identity, legacy configuration, protobuf feature capabilities, connection-ready, battery, time, sync/file announcements, directory/file completion, notification subscription/control point, unknown messages, and protocol problems.

UI code should speak/render semantic state, not packet hex. Unknown values are preserved and surfaced rather than silently guessed.

## Pairing

Visible pairing uses a `passkey_provider(mode, timeout_seconds)` callback. The callback returns the decimal passkey shown by the watch. OOB pairing accepts an explicit 16-byte passkey. `AuthProtocolEngine.cancel_pairing()` is the application-level user-cancel path. A successful session installs secure packet wrapping only after the plaintext 5111 acknowledgement is sent.

## Capability gating

Do not assume optional watch features. The client first consumes legacy Configuration 5050. Smart/Core feature capabilities are queried only when peer flag 95 is present; GNCS requires host flag 6. Time uses the 5052 request path when peer flag 71 is present, otherwise the legacy 5026 settings path is used.


## Experimental next-generation FileAccess

For watches that advertise legacy flag 90, `FileAccessControlClient` implements protobuf item listing/pull/status handling. `MultiLinkClient` implements the recovered MultiLink registration channel and `FileAccessMlrDownloader` joins it to the clean-room MLR data path. The current downloader deliberately requests **uncompressed** reads and uses conservative immediate cumulative ACKs.

```python
control = client.file_access_control
ml = MultiLinkClient(
    backend,
    transport.services,
    connection_id=my_stable_nonzero_app_id,
    max_write_length=transport.write_payload_size,
)
await ml.initialize()
listing = await control.list_items()
download = await FileAccessMlrDownloader(control, ml).download(listing.items[0])
```

`connection_id` is the independent application's stable MultiLink identity. The CLI default is project-defined wire text `GPLAB001`; it is not a Garmin identifier. Pulls default to uncompressed data, with optional standard-zlib decompression when requested. Local transfer failure sends best-effort FileAccess Cancel Transfer before cleanup. This path is offline-tested but remains experimental until a target watch validates service registration, reliable timing and recovery.

## Hardware workflow

The reference CLI exercises the same API:

```bash
uv run garmin-proto scan --seconds 8
uv run garmin-proto services <address>
uv run garmin-proto probe <address>
uv run garmin-proto pair <address>
uv run garmin-proto workflow <address> --download-first-activity
```

Raw addresses are redacted by default in discovery output. Pairing secrets and session keys are not printed. `workflow` is an integration probe, not evidence of completion until the designated watch/firmware passes the matrix in `GOAL.md`.
