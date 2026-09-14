# Hardware verification runbook

Use the owner's watch/account. Raw captures stay under ignored `captures/`; committed evidence is sanitized.

## 1. Baseline

Record watch model/firmware, host OS/version, Garmin Connect reference version, current OS bond state and current GFDI pairing state. Do not commit serials, MAC addresses, account IDs or key material.

## 2. Reference Garmin Connect captures

Enable Android Bluetooth HCI snoop logging and record one operation per capture:

1. clean GATT discovery;
2. fresh system-bond pairing and, as a separate capture, fresh unbonded Garmin-auth 5101–5111 pairing;
3. reconnect after app restart;
4. reconnect after Bluetooth toggle;
5. reconnect after watch reboot;
6. one device-info/battery exchange;
7. one time sync;
8. notification subscribe + source + one supported action;
9. legacy file types + directory + one activity/health download;
10. FileAccess capability/listing + MultiLink registration + one activity/monitoring/sleep pull when advertised;
11. interrupt one large pull and record resume/recovery.

Collect a bug report with `adb bugreport captures/<name>.zip` when available. Raw archives are never committed.

## 3. Independent client on ferpc

Start one Bluetooth monitor capture per experiment:

```bash
mkdir -p captures
btmon -w captures/independent.btsnoop
```

Run the client in another terminal:

```bash
uv run garmin-proto scan --seconds 8
uv run garmin-proto services <address>
uv run garmin-proto probe <address>
uv run garmin-proto reset-pairing <address>
uv run garmin-proto pair <address>
uv run garmin-proto fitness-sync <address> --output ~/Garmin-FIT
```

For a fresh-pair run, `reset-pairing` removes the saved GFDI record and the BlueZ bond first. Default `pair` follows Garmin Connect’s bonded route: create the OS bond, then complete GFDI with proprietary Garmin auth disabled. On Linux a temporary terminal `KeyboardDisplay` BlueZ agent handles passkey entry/confirmation. Run a second controlled case with `pair <address> --garmin-auth` to exercise 5101–5111 and LTK/EDIV/RAND persistence without system bonding. On the bonded route, later runs detect and reuse the existing BlueZ bond. On the unbonded Garmin-auth route, persisted LTK/EDIV/RAND drives 5102 reconnect without requesting a system bond. Use `--system-bond` to force a bond on a saved pairing record for comparison.

Protocol-focused probes remain available:

```bash
uv run garmin-proto workflow <address>
uv run garmin-proto workflow <address> --download-first-activity
uv run garmin-proto workflow <address> --next-gen-files --skip-files
uv run garmin-proto workflow <address> --download-first-next-gen-fitness --skip-files
```

Stop `btmon` immediately after the intended state transition.

## 4. Correlation record

For every baseline GATT operation, create a sanitized `D-####` observation with direction, service/characteristic, operation, length and protocol interpretation. Link it to the corresponding `S-####` static evidence and `P-####` protocol fact. Replace secret bytes with their role and length.

Record these before completing M2:

- active GFDI physical route: dedicated pair or MultiLink service 1, plus the selected read/write characteristics;
- negotiated MTU and subscription order;
- 5024/5050 startup order;
- 5101–5111 fresh-pair order and user prompt;
- default system-bond result and reuse of the existing OS bond on reconnect;
- separate unbonded Garmin-auth LTK/EDIV/RAND persistence and 5102 reconnect;
- persisted LTK/EDIV/RAND reconnect behavior;
- flags 90/95 on the target;
- MultiLink characteristic pair, registration service 4 and returned FileAccess reliable handle;
- read-pipe configure, MLR SN/RN progression, ACK timing, Transfer Status and checksum lifecycle.

## 5. Recovery matrix

After a successful pair, rerun after each isolated condition:

- client restart;
- Bluetooth toggle;
- watch reboot;
- host reboot;
- out-of-range reconnect;
- deliberate GFDI/Protobuf request timeout;
- dropped MLR ACK / retransmission timeout;
- interrupted FileAccess pull followed by `.part` resume;
- compressed FileAccess pull;
- legacy compressed pull and uncompressed fallback.

The final acceptance run is performed with Garmin Connect stopped or absent. Update the protocol spec, add sanitized fixtures/tests, attach `D-####` evidence and only then check the corresponding `GOAL.md` requirements.
