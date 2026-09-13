# Investigation record

This is the concise research record for the public repository. Detailed generated decompiler output and raw captures remain local and are intentionally not published.

## Scope

The studied input is Garmin Connect 5.29 for Android, acquired as an APKM and analyzed locally with JADX and apktool/smali. The goal is interoperability with an owner's watch, not cloning Garmin Connect. Protocol facts are documented independently in `spec/PROTOCOL.md` and implemented in `src/garmin_proto_lab/`.

No existing third-party Garmin protocol implementation has been used as an implementation source.

## Reconstructed stack

```text
Android/BlueZ BLE
  -> GATT service + read/write characteristics
  -> notification/write byte stream
  -> zero-delimited COBS
  -> GFDI frame + CRC-16
  -> optional XXTEA secure-session wrapper
  -> request/response dispatcher
  -> device/config/battery/time/file/GNCS + Smart protobuf handlers
```

Static evidence indicates Garmin Connect requests ATT MTU 515 and treats notification boundaries as arbitrary stream chunks rather than protocol packet boundaries.

## BLE identifiers

The strongest statically identified GFDI endpoints are:

| Role | UUID |
|---|---|
| Connect Mobile service | `9b012401-bc30-ce9a-e111-0f67e491abde` |
| host write | `df334c80-e6a7-d082-274d-78fc66f85e16` |
| host notify/read | `4acbcd28-7425-868e-f447-915c8f00d0cb` |
| generic GFDI write | `6a4e4c80-667b-11e3-949a-0800200c9a66` |
| generic GFDI notify/read | `6a4ecd28-667b-11e3-949a-0800200c9a66` |

A real watch must still confirm which service contains the active pair.

## Wire protocol

Plain GFDI frames use a little-endian length, a message identifier (or compact transaction form), payload, and CRC-16/ARC. Frames are COBS encoded and carried between zero delimiters.

Authentication messages 5101-5112 establish XXTEA-based pairing/session state. Persistent pairing material is LTK + EDIV + RAND; per-connection SKD/session key/IV/counters are ephemeral. The implementation keeps these layers separate and never logs secret material intentionally.

Important feature families recovered for the v1 workflow include:

- 5024 device information
- 5023 battery status
- 5026/5030/5052 time and settings
- 5002/5004/5007/5008/5031/5054 file transfer and directory operations
- 5033-5036 GNCS notifications
- 5101-5112 authentication/session

The complete working message census and field layouts are in `spec/PROTOCOL.md`.

## Offline implementation status

The reference library includes transport selection, framing, authentication/session orchestration, semantic handshake, GNCS codecs, file listing/download state, FIT-file inspection, and a CLI. Property tests exercise COBS/frame round trips, malformed inputs, stream fragmentation, secure counters, file CRC/offset handling, protobuf chunking, and related parser bounds.

Offline tests are evidence for implementation consistency only. They cannot upgrade watch-specific facts to `CONFIRMED`.

## Hardware verification queue

The next watch session should be captured as small, controlled experiments:

1. clean BLE service/characteristic discovery and MTU/subscription order;
2. fresh pairing, including the 5101/5103-5111 sequence and any user prompt;
3. reconnect after client restart, Bluetooth toggle, watch reboot, and host reboot;
4. one isolated device-info/battery/time exchange each;
5. notification subscribe, delivery, action/dismissal where supported;
6. file-type query, directory listing, one representative activity/health download, then an interrupted larger transfer;
7. repeat with Garmin Connect stopped/uninstalled to prove runtime independence.

Raw captures must stay outside version control. Sanitized observations should be assigned `D-####` identifiers and correlated with the static `S-####` evidence before protocol facts are upgraded.

## Known blocker

The target watch is not currently present at the development machine. That blocks the M2 dynamic baseline and every completion requirement that explicitly demands on-watch verification. The project must not mark those requirements complete based only on static analysis or simulation.
