# Investigation record

This is the concise public research record. Generated decompiler output, Garmin binaries and raw captures remain local.

## Inputs and method

Primary input is Garmin Connect 5.29 for Android, analyzed with JADX, Apktool/smali, Ghidra, Rizin/objdump and targeted Python extraction scripts. The x86_64 `libreliable-ml.so` split was analyzed locally for the MultiLink Reliable state machine. Public Garmin BLE work such as Gadgetbridge-derived Garmin Bridge was reviewed as a cross-reference after the APK/native reconstruction.

Protocol details and evidence IDs are maintained in `spec/PROTOCOL.md` and `evidence/static/STATIC_MAP.md`; executable behavior is in `src/garmin_proto_lab/`.

## Reconstructed stack

```text
BLE GATT
  -> Garmin service/read/write characteristics
  -> zero-delimited COBS byte stream
  -> GFDI frame + CRC-16/ARC
  -> optional XXTEA secure wrapper
  -> GFDI request/response dispatcher
       -> authentication 5101..5112
       -> configuration/device/time/battery
       -> legacy file transfer
       -> GNCS notifications
       -> Smart protobuf 5043/5044/5045
            -> FileAccess field 43
            -> MultiLink registration
            -> MLR reliable transport
            -> FIT file bytes
```

Garmin Connect requests ATT MTU 515. Notification callback boundaries are stream chunks, not GFDI packet boundaries.

## Pairing finding

Garmin Connect distinguishes first-time system bonding from persisted GFDI authentication. `o72/C37070a.m55705b()` is true when LTK, EDIV, or RAND exists. `w72/C50242e.m69878c` forwards the caller's bond requirement when those fields are absent, but when saved auth material exists it registers the record in `AuthRegistry` and forces `requiresBond=false`.

The fresh pairing strategy `zj2/C55757b.m75743o` constructs a record without LTK/EDIV/RAND and calls the connection manager with system bonding enabled. `yg2/C54129v` owns `BluetoothDevice.createBond()` with a 45-second timeout. After the BLE connection is established, `DefaultAuthDelegate.isGarminAuthAllowed()` returns false for a bonded remote (except the explicit `VIVO` manufacturer special case), so `aq2/C2861i` installs `bq2/C4587y` StubAuthHandler instead of the 5101–5111 AuthHandler. StubAuth completes with null Garmin session keys and fails if 5101 arrives. The saved Garmin-auth reconnect path instead supplies LTK/EDIV/RAND, skips a new system bond request, and runs the proprietary 5102/session sequence.

The implementation mirrors both routes. With no Garmin-auth record, `pair`, `workflow`, and `fitness-sync` require the OS bond; the backend first checks the existing BlueZ paired state, so an already bonded watch is not paired again. The separate `--garmin-auth` route skips the OS bond, runs 5101–5111, persists LTK/EDIV/RAND, and uses that record for 5102 reconnect. `--system-bond` forces the bonded route for diagnostics.

Authentication messages 5101–5112 establish the Garmin session. Static dataflow resolves the previously unnamed fields used by the required workflow:

- 5103 bytes 3–4 are read by no branch and are preserved as reserved bytes.
- 5109 decrypts 24 bytes, compares only bytes 0–15 to the session key, and ignores/reserves bytes 16–23.
- 5111 uses decrypted byte 0 plus device IV bytes 8–11. Bytes 1–7 and 12–15 are reserved on input; the response zero-initializes bytes 1–7, generates host IV bytes 8–11 and random bytes 12–15.
- 5107 decrypts 32 bytes and persists only LTK 0–15, EDIV 16–17 and RAND 18–25; bytes 26–31 are reserved.

Persistent reconnect material is LTK + EDIV + RAND. SKD contributions, session key, IVs and secure packet counters are regenerated per connection. `AuthRegistry` callback order is EDIV, RAND, LTK; Garmin's connection worker persists those values under `GBLE_DIV_KEY`, `GBLE_RAND_KEY`, and `GBLE_LTK_KEY`, with no per-session crypto material in that persistence path.


## File extraction

Two watch-to-host fitness paths are implemented.

The legacy path uses Supported File Types 5031, Directory Filter 5007, directory index 0, Download File 5002 and File Data 5004/5054. Data type 128 plus FIT subtype identifies activity/health objects; the downloaded FIT File ID is checked against the directory subtype.

The next-generation path uses Configuration flag 90 and Smart FileAccess extension 43. Static handler selection confirms peer flag 90, not flag 95, decides whether Garmin creates the Sync2/FileAccess manager; flag 95 only enables the optional feature-capability query. The recovered service schema contains fields 1–26 for pull/push, transfer status, priority changes, listing, notifications, delete/modify flags, cancellation, resource/sync notifications, software part numbers and checksums. Fitness sync agents identify `FIT_TYPE_4` activity, `FIT_TYPE_32` monitoring and `FIT_TYPE_49` sleep, plus the other modeled health FIT types.

`GetItemChecksum` result method `TRUNCATED_MD5` is the first eight MD5 digest bytes interpreted as a little-endian uint64 and serialized as protobuf fixed64.

## MultiLink and MLR

FileAccess data travels over Garmin MultiLink service `6A4E2800-667B-11E3-949A-0800200C9A66`. Registration uses service ID 4 and reliable flag `0x02`; FileAccess pipe services are `0x2018, 0x4018, 0x6018, 0x8018, 0xA018, 0xC018, 0xE018`.

Native analysis resolves the complete MLR behavior required by the read path:

- 64 sequence/request values for normal connections; reliable handles `0x80..0x87`.
- payload capacity `max_write_length - 2` and packet-count formula `(length + max_write_length - 3) // (max_write_length - 2)`.
- initial send window 32 packets, growth by one on an advancing ACK, maximum 63.
- cumulative six-bit request number (RN) ACKs and six-bit sequence number (SN).
- ACK after five received data packets or a 10 ms deferred-ACK timer; outbound traffic piggybacks the current RN.
- initial RTO 1000 ms, minimum computed RTO 500 ms, timeout doubling capped at 20,000 ms.
- RTT estimator alpha `1/8`, beta `1/4`, RTO term `sRTT + max(0.5*sRTT, 4*RTTVAR)`.
- timeout halves the send window and retransmits from the unacknowledged base.

Four native formatter self-test vectors are included in the automated tests: `05 01`, `d0 c2 ff`, `92 cd ff de a2`, `ff ff 01 02 03`.

## Fitness-file integrity and persistence

The extractor validates FIT header signature/size/header CRC, parses File ID global message 0, and verifies the trailing FIT file CRC. FileAccess checksum verification is added when the watch advertises truncated-MD5 support.

`fitness-sync` writes incoming clear bytes to mode-`0600` `.part` files, resumes a subsequent FileAccess pull at the saved byte length, then atomically renames the verified file. Output directories are mode `0700`. A valid already-complete file is reused without another download.

For local interpretation, the public Garmin FIT Python SDK profile at revision `f0d86b18195dbdf9c5b2f135aad5d6ae541f5fbb` (FIT Profile 21.214.0) was used as a field-profile cross-reference. The runtime now projects standard activity and wellness messages—including weight/body composition, blood pressure, monitoring, HRV, resting heart rate, stress, SpO2, sleep levels, respiration rate, and Body Battery—while preserving the raw numeric FIT fields. The SDK is not a runtime dependency.

## Hardware verification queue

When the watch is available, run isolated captures for:

1. service/characteristic discovery, MTU and subscription order;
2. fresh system-bond pairing, plus a separate unbonded 5101–5111 Garmin-auth run;
3. reconnect after client restart, Bluetooth toggle, watch reboot and host reboot;
4. device-info, battery and time requests;
5. FileAccess capability/listing plus one activity, monitoring and sleep file where present;
6. legacy directory/download on the same watch for comparison;
7. interrupted FileAccess transfer followed by byte-offset resume and checksum/FIT-CRC validation;
8. final run with Garmin Connect stopped or absent.

Raw captures stay outside version control. Sanitized observations receive `D-####` identifiers and are linked to the matching static/protocol facts.

## Current blocker

The target watch is not present at the development machine. Static analysis and offline implementation no longer block the required pairing/fitness workflow; the remaining unchecked completion items are the device-execution matrix above.

## MultiLink identity and GFDI route

The bundled `assets/client_config.xml` declares client_uuid `0x01`; that value is passed directly into the MultiLink communicator and serialized as the uint64 registration connection ID. The implementation now defaults to `0x01`. Watches without a dedicated GFDI pair are handled through logical MultiLink service 1, with reliable MLR when the peer returns a reliable handle.
