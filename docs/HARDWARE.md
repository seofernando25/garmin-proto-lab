# Hardware verification runbook

Use this only with the owner's watch/account. Raw captures stay under ignored `captures/`; committed evidence must be sanitized.

## 1. Record device baseline

Before changing pairing state, record watch model/firmware, host OS/version, Garmin Connect version used for the reference trace, and whether Bluetooth/Garmin pairing already exists. Do not commit serials, MAC addresses, account IDs, or pairing secrets.

## 2. Reference Garmin Connect trace

On an Android test phone, enable **Bluetooth HCI snoop log**, then perform one operation per capture. Export the bug report/HCI log after each run. The minimum reference sequence is:

1. clean BLE service discovery;
2. fresh pair/authentication;
3. reconnect after app restart;
4. reconnect after Bluetooth toggle;
5. reconnect after watch reboot;
6. one device-info/battery exchange;
7. one time sync;
8. notification subscribe + one notification + one supported action/dismissal;
9. file-type query + directory listing + one representative activity/health download; if flag 90 is present, also record FileAccess/MultiLink registration and the first reliable packets;
10. interrupt one larger download and observe restart/resume behavior.

When `adb` is available, a bug report can be collected with `adb bugreport captures/<name>.zip`. Never commit the raw archive.

## 3. Independent-client trace on ferpc

BlueZ `btmon` writes btsnoop directly, but opening the HCI monitor channel may require root or suitable Linux capabilities:

```bash
mkdir -p captures
btmon -w captures/independent.btsnoop
```

In another terminal, progress from least invasive to full workflow:

```bash
uv run garmin-proto scan --seconds 8
uv run garmin-proto services <address>
uv run garmin-proto probe <address>
uv run garmin-proto workflow <address>
uv run garmin-proto workflow <address> --download-first-activity
```

Stop `btmon` after the single intended experiment. Use a new capture for each state transition instead of recording a long mixed session.

## 4. Required correlation

For every baseline GATT operation, record a sanitized `D-####` observation containing direction, service/characteristic, operation type, length, and protocol interpretation. Correlate it to the static `S-####` path or mark it unresolved. Secret bytes are replaced by their role and length, not copied into notes.

Minimum facts to confirm before checking M2 are: active GFDI service/characteristic pair, negotiated MTU, subscription order, 5024/5050 ordering, 5101-5111 pairing/reconnect order, persistence boundary, and whether Smart protobuf flag 95 is used by this watch. If configuration flag 90 is present, also confirm the MultiLink 0x281x/0x282x pair, registration service 4, a reliable FileAccess service ID, and the read-pipe configure/status lifecycle before enabling next-gen downloads by default.

## 5. Recovery matrix

After one successful independent pair, rerun the workflow after each isolated condition: client restart, Bluetooth toggle, watch reboot, host reboot, out-of-range/reconnect, deliberate request timeout, and interrupted large transfer. Garmin Connect must be stopped/uninstalled for the final v1 integration pass.

The project is not complete because a trace "looks right". Update `spec/PROTOCOL.md`, add sanitized tests/fixtures where possible, link the `D-####` evidence, then check the matching `GOAL.md` requirement.
