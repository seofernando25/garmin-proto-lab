# Static Evidence Map — Garmin Connect 5.29

This file is a research index, not source code for the compatibility library. Paths under `analysis/` are local generated evidence and are excluded from git. The runtime implementation in `src/garmin_proto_lab/` is written from the protocol facts in `spec/PROTOCOL.md`.

## S-0001 — Artifact provenance and bundle inventory

The research input is the APKMirror Garmin Connect 5.29 APKM already acquired for this project. `analysis/inventory/artifacts.json` records package/version/SDK metadata, sizes, SHA-256 hashes, signing scheme and certificate fingerprint. `analysis/inventory/bundle_inventory.json` lists all 33 APKs, 14 DEX entries, split architectures/DPIs/languages and 96 native-library paths. `scripts/extract_bundle.py` safely re-extracts the APKM and emits per-entry hashes; `evidence/logs/bundle-reproducibility.txt` shows a fresh extraction produced an identical `base.apk`.

Primary generated evidence:

- `analysis/inventory/artifacts.json`
- `analysis/inventory/bundle_inventory.json`
- `evidence/logs/inventory.txt`
- `evidence/logs/bundle-reproducibility.txt`

## S-0002 — Independent decoding views

The same hashed `base.apk` was decoded by two independent representations:

- Apktool 3.0.3 -> resources, manifest and smali in `analysis/apktool/`; successful log at `evidence/logs/apktool-decode.log`.
- JADX 1.5.6 -> Java/Kotlin-like source in `analysis/jadx/`. The high-level pass recovered roughly 60k Java files but reported decompilation failures on complex methods. A separate JADX `fallback` pass completed successfully and is retained in `analysis/jadx-fallback/` for instruction-level recovery. Complex methods are checked against Apktool smali rather than trusted solely from JADX.

Tool versions and acquisition hashes are in `analysis/inventory/toolchain.json`.

## S-0003 — Manifest/Bluetooth surface

`analysis/inventory/manifest_map.json` is generated from the Apktool manifest. The app declares legacy and modern Bluetooth permissions, including `BLUETOOTH_CONNECT`, `BLUETOOTH_SCAN` (`neverForLocation`), foreground connected-device service capability, companion-device foreground-service capability, network/location/storage/notification/wakelock permissions, and required Bluetooth hardware. BLE/device/pairing/sync-related Android components are listed in the same report.

This is only the Android permission/component surface; it does not imply that every listed component participates in the watch protocol.

## S-0004 — BLE connection state machine and byte stream

The generic BLE implementation is concentrated in package `yg2` and stream wrappers in `zg2`.

Key evidence:

- `analysis/jadx/sources/yg2/C54129v.java:1329-1378` names the lifecycle states `NOT_STARTED`, `SCANNING`, `CONNECTING_GATT`, `WAITING_FOR_BOND`, `DISCOVERING_SERVICES`, `AVAILABLE`, `DISCONNECTING`, `FINISHED`.
- `C54129v.java:584-613` calls Android `connectGatt(..., TRANSPORT_LE)` and `discoverServices()`.
- `analysis/jadx/sources/yg2/CallableC54115h.java` requests ATT MTU 515; the surrounding BLE abstraction exposes a characteristic payload size of negotiated MTU minus 3, with a legacy default of 20 bytes.
- `analysis/jadx/sources/yg2/C54109b.java` serializes GATT operations and receives read/write/notification/MTU callbacks.
- `analysis/jadx/sources/com/garmin/android/gfdi/framework/PacketQueue.java` bounds the packet queue, retries failed writes and spaces packets.
- `analysis/jadx/sources/zg2/C55683b.java` slices an output byte stream into consecutive GATT characteristic writes using the current maximum characteristic payload. It adds no Garmin-specific per-fragment header.
- `analysis/jadx/sources/zg2/C55682a.java` appends characteristic notification payloads directly into one input byte stream. Notification boundaries are therefore not protocol-frame boundaries.

`analysis/inventory/ble_call_sites.json` indexes direct/wrapped Android BLE API call sites across the decompiled tree.

## S-0005 — Garmin BLE UUID registry

`analysis/inventory/uuid_inventory.json` contains 103 unique literal UUIDs and classifies 32 as Garmin-specific, 16 as Bluetooth-base UUIDs, 4 as unrelated third-party framework UUIDs and 51 conservatively unresolved.

The watch-facing registry at `analysis/jadx/sources/x72/C52193h.java:68-81` establishes, among others:

- Connect Mobile service: `9B012401-BC30-CE9A-E111-0F67E491ABDE`.
- Connect Mobile write characteristic: `DF334C80-E6A7-D082-274D-78FC66F85E16`.
- Connect Mobile read/notify characteristic: `4ACBCD28-7425-868E-F447-915C8F00D0CB`.
- Generic GFDI write/read characteristics: `6A4E4C80-667B-11E3-949A-0800200C9A66` and `6A4ECD28-667B-11E3-949A-0800200C9A66`.
- Garmin realtime and multilink service families under the `6A4E....-667B-11E3-949A-0800200C9A66` namespace.

`analysis/apktool/assets/client_config.xml` identifies the Connect Mobile client configuration and supported service families. This static mapping still needs service-discovery confirmation on the owner's watch.

## S-0006 — GFDI framing, COBS and dispatcher

The GFDI layer is a byte-stream protocol above the BLE characteristics.

Key evidence:

- `analysis/jadx/sources/lq2/C33762d.java:61-90` constructs outbound frames and computes a running CRC.
- `analysis/jadx/sources/rq2/C42755e.java:98-100` wraps each encoded frame in zero delimiters; the class implements standard COBS encoding.
- `analysis/jadx/sources/rq2/C42754d.java` scans the stream from one zero delimiter to the next with a bounded encoded-packet size.
- The synthetic reader path in `analysis/jadx/sources/p273j1/RunnableC28158a.java` recovers total length, ordinary/transaction message type, payload and trailing CRC.
- `analysis/jadx/sources/kp2/C31753q4.java` is a reflected 16-bit CRC update with polynomial `0xA001`, initial state zero at frame start.
- `analysis/jadx/sources/dq2/C19068b.java` and `C19076j.java` implement request/response dispatch, transaction matching, unknown-message handling and the default request timeout.

The independent Python implementation cross-checks CRC against the standard `"123456789" -> 0xBB3D` vector and round-trips COBS/frame encodings under property tests.

## S-0007 — Authentication and secure-session path

Authentication is implemented in the managed GFDI layer; no protocol-path JNI boundary has been found.

Key evidence:

- `analysis/jadx/sources/bq2/C4563a.java` advertises XXTEA as the active supported authentication algorithm and constructs 16-byte randomness/confirm values.
- `analysis/jadx/sources/bq2/C4576n.java:19-84` implements 32-bit little-endian XXTEA with delta `0x9E3779B9`.
- `analysis/jadx/sources/bq2/C4565c.java:179-184` registers authentication/session message IDs 5101, 5103, 5107, 5108, 5109 and 5111; the same class sends 5104/5105/5106 while executing fresh-pairing flows.
- `C4565c.java:553-759` handles negotiation, passkey mode, LTK distribution, session-key distribution/verification and secure-session setup.
- `analysis/apktool/smali_classes7/bq2/c.smali` was used to recover complex coroutine branches that JADX could not reliably structure.
- `analysis/jadx/sources/bq2/InterfaceC4585w.java` shows the visible decimal passkey is parsed as a signed 32-bit integer, serialized little-endian and zero-padded to 16 bytes before use.
- `analysis/jadx/sources/bq2/C4578p.java:33-45` applies the outbound secure-session packet wrapper and monotonically increasing packet counter.
- `analysis/jadx/sources/bq2/C4577o.java:37-73` validates inbound outer length, decrypts, checks packet counter and device diversifier/IV, then returns the inner GFDI frame.

The generic XXTEA implementation in `src/garmin_proto_lab/xxtea.py` was independently cross-checked against the published Wheeler/Needham `btea` formulation compiled locally with `uint32_t`; it is not copied from Garmin code.

## S-0008 — Handshake and basic semantic messages

Static handlers expose several high-value message grammars:

- Device Information 5024: `analysis/jadx/sources/jq2/C29529a.java:427-536`. Fixed fields are protocol version, product number, unit ID, software version and max packet size followed by three one-byte-length strings. For products supporting the extended section, dual-pairing state and reversed BLE/classic MAC values follow. Products 2787, 3192 and 3307 bypass this extended parse.
- Battery Status 5023: `analysis/jadx/sources/s72/C43748a.java` and subclasses. The battery state occupies mask `0x70`; capacity percentage is carried separately.
- Current Time 5052: `analysis/jadx/sources/eq2/C20662a.java:189-228`. The response echoes four request bytes, then writes Garmin-epoch current time, UTC offset, next DST-start and next DST-end values. Garmin epoch offset from Unix is 631065600 seconds.
- Configuration 5050 and Queued Download 5027 are also handled by the handshake package.

These message interpretations are currently static `SUPPORTED` facts pending a watch trace.

## S-0009 — File-transfer semantics

Static file-transfer handlers reveal both standard and compressed chunking.

Read/watch-to-host:

- `analysis/jadx/sources/gq2/C23676c.java:185-287` sends Download File 5002 and parses status/file size.
- `analysis/jadx/sources/gq2/C23682i.java` parses ordinary File Data 5004. Payload layout is a one-byte transfer marker, uint16 running CRC, uint32 data offset, then data bytes. Responses contain status plus current offset. Duplicate/mismatched offsets are explicitly retried/aborted.
- `analysis/jadx/sources/gq2/C23674a.java` parses Compressed File Data 5054: packet counter, flags, cumulative decompressed position, running CRC and compressed bytes, with DEFLATE decompression and CRC validation.

Write/host-to-watch:

- `analysis/jadx/sources/hq2/C25389o.java` sends Upload File 5003 with file size/resume metadata and validates resume offset/CRC/free-space status.
- `analysis/jadx/sources/hq2/C25390p.java` builds ordinary File Data 5004 chunks with seven bytes of transfer overhead and running CRC/offset state.
- `analysis/jadx/sources/hq2/C25375a.java` builds compressed File Data 5054 chunks and validates packet-counter/error responses.
- `analysis/jadx/sources/com/garmin/gfdi/file/C12034a.java` implements directory filter 5007, file flags 5008, supported file types 5031, incoming data 5004/5054 and cancel 5022.

This is enough to define an offline file-transfer codec/state model, but not to claim activity sync works without dynamic confirmation of file indexes/types and the target watch's capabilities.

## S-0010 — Notification path

`com/garmin/android/gncs` exposes Garmin Notification Communication Service semantics above GFDI.

Static findings include:

- Message 5036 subscription request: two bytes, intent (unsubscribe/subscribe) and feature flags; response includes status, echoed intent and negotiated feature flags.
- Message 5035 data-source transfer: total ANCS payload size, running CRC, data offset and chunk bytes; response statuses include success, resend, abort, CRC mismatch and data-offset mismatch.
- `SmartNotificationsDataHandler` registers 5033 notification source, 5034 control point, 5035 data source and 5036 subscription.

Notification payload semantics also traverse Android notification/ANCS-like and protobuf-capability layers, so full notification reconstruction remains incomplete without further static work and device testing.

## S-0011 — Native-code boundary

`analysis/inventory/native_boundary.json` inventories native libraries and all `System.load*`/Java `native` declarations found by the static scan. The bundle contains many native libraries, but the scan found zero load/native declarations in the identified watch-protocol namespaces (`gfdi`, `yg2`, `aq2`, `bq2`, `dq2`, `lq2`, `x72` and device BLE/pairing namespaces). Current evidence therefore supports keeping the protocol implementation entirely managed-language; native libraries remain available for targeted inspection if a future static call path crosses JNI.

## S-0012 — Static dependency graph

The watch-facing stack recovered so far is:

```text
Android BluetoothLeScanner / BluetoothGatt
        |
        v
yg2 BLE connection state machine + serialized GATT operations
        |
        v
zg2 read-notification byte stream / write chunk stream
        |
        v
Garmin service subscriber (service + read/write characteristic selection)
        |
        v
COBS-delimited byte stream (rq2)
        |
        v
GFDI plaintext frame + CRC (lq2 / reader synthetic path)
        |
        +--> authentication/session (bq2) --> optional XXTEA secure wrapper
        |
        v
message dispatcher / request-response transactions (dq2)
        |
        +--> handshake/device info/config (jq2)
        +--> battery/status (s72)
        +--> time (eq2)
        +--> file transfer (com.garmin.gfdi.file, gq2, hq2)
        +--> smart notifications/GNCS (com.garmin.android.gncs)
        +--> protobuf and other feature handlers
```

This graph is the working map for clean-room reconstruction. Every runtime module is to consume protocol facts, not Garmin classes.

## S-0013 — Static-analysis completeness limits

The static pass is deliberately conservative. JADX recovered the overwhelming majority of source-like output but reported failures in complex coroutine methods, so any such method is resolved using fallback output and Apktool smali before becoming a protocol fact. Literal UUID classification leaves unresolved values when static evidence is insufficient. No dynamic assertion is upgraded to `CONFIRMED` while the watch is absent.


## S-0014 — GNCS/ANCS notification payload grammar

Further static analysis resolves the notification payload carried inside the GFDI GNCS messages rather than only the outer 5033/5034/5035/5036 transport. The relevant evidence is under `analysis/jadx/sources/com/garmin/android/ancs/` plus `SmartNotificationsDataHandler` in `com/garmin/android/gncs`.

Key interoperability facts:

- GNCS Notification Source 5033 carries a nine-byte ANCS-shaped event: event ID, event-flag bitset, category, capped category count, uint32 little-endian notification ID, and feature flags.
- Event IDs are added/modified/removed = 0/1/2. Event flags are silent `0x01`, important `0x02`, pre-existing `0x04`, positive action `0x08`, negative action `0x10`.
- Category IDs span other through SMS (`0..12`). Feature flags indicate phone-number availability (`0x01`), Android actions (`0x02`) and media (`0x04`).
- GNCS Control Point 5034 transports commands to fetch notification attributes, fetch app attributes, perform the standard positive/negative action, or perform an Android-specific action. Notification/app attribute responses are returned through GNCS Data Source 5035.
- Notification-attribute requests carry a uint32 little-endian notification ID and attribute selectors. String-like selectors carry requested length limits; the Android-actions selector additionally carries maximum action count and action flags. Attribute responses are a sequence of one-byte attribute IDs plus uint16 little-endian lengths and raw values.
- Android action descriptors contain action ID, flag bits, one-byte title length and title bytes. Action flags distinguish input, positive, negative and dismissal semantics.
- The 5033 sender transmits the nine-byte payload directly when no GNCS session key is available; when a session key exists it first applies the same self-describing padded XXTEA transform used by the 5035 GNCS data path.
- The 5034 receiver symmetrically decrypts padded XXTEA when a GNCS session key exists, validates the clear ANCS command, and returns two response-specific bytes: GNCS control-point result plus ANCS semantic error. Recovered semantic errors are no-error 0, unknown command 160, invalid command 161 and invalid parameter 162.

Primary generated evidence:

- `analysis/jadx/sources/com/garmin/android/ancs/ANCSMessageBase.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSNotificationSource.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSGetNotificationAttributesRequest.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSGetNotificationAttributesResponse.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSGetAppAttributesRequest.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSGetAppAttributesResponse.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSNotificationAction.java` and `ANCSNotificationActions.java`
- `analysis/jadx/sources/com/garmin/android/ancs/ANCSPerformNotificationAction.java` and `ANCSPerformAndroidAction.java`
- `analysis/jadx/sources/com/garmin/android/gncs/SmartNotificationsDataHandler.java`

These facts resolve the offline payload grammar, but target-watch subscription order, supported attributes/actions and user-visible behavior remain dynamic questions.


## S-0015 — Legacy sync intents, queued downloads and File Ready

The legacy sync manager at `analysis/jadx/sources/eq2/C20670i.java` resolves the outer synchronization intent that precedes file transfer. It registers 5009 File Ready, 5027 Queued Download and 5037 Sync Request.

- 5037 contains a one-byte visibility/mode value followed by a one-byte bitset length and that many download-category bytes. Values 0/1/2 map to manual, invisible and visible-as-needed; an unknown mode is logged and treated conservatively by the Garmin app.
- 5027 contains a one-byte bitset length followed by the same category bitset and is acknowledged with response data byte 0.
- 5009 has a 16-byte minimum fixed body: uint16 file index, one-byte data type, three-byte identifier, one preserved byte at offset 6 whose role is unresolved, one file-flag byte, uint32 file size and uint32 timestamp/value. The sync bridge treats the first identifier byte as file subtype.
- File-flag masks recovered from `com/garmin/gfdi/file/EnumC12038e.java` are READ `0x80`, WRITE `0x40`, ERASE `0x20`, ARCHIVE `0x10`, APPEND `0x08`, CRYPTO `0x04`.
- `analysis/jadx/sources/pv2/EnumC39930g.java` maps download-category bit positions to names used by the higher-level sync queue. Relevant examples include activities=5, software-update=8, device-settings=9, activity-summary=21, sleep=26, HRV-status=36 and uninstall-app=39. The complete recovered mapping is represented independently in `src/garmin_proto_lab/sync.py`.

This mapping identifies the generic category/request grammar. It does not identify which concrete 5009 file index/data-type/subtype represents activity or health data on the owner's watch; that remains a D-PLAN-0005 question.


## S-0016 — File directory enumeration and read-side transfer lifecycle

Static reconstruction of `com/garmin/gfdi/file/C12034a.java`, `gq2/C23676c.java`, `gq2/C23682i.java`, `gq2/C23674a.java`, `fq2/EnumC22108b.java` and `FileManagerCompat` establishes the complete generic read-side path without assuming target-specific file indices.

- Message 5007 applies a one-byte directory filter: 0 no-filter, 1 default, 3 pending-uploads-only. A response data byte of zero means the device supports the filter; nonzero means it does not.
- Message 5031 with empty request queries supported file types. Successful response byte 0 is an entry count followed by `dataType:u8 | subType:u8 | nameLength:u8 | name`. If the device explicitly returns UNKNOWN_OR_NOT_SUPPORTED, the app uses two legacy mappings: `(128,4,"FIT_TYPE_4")` and `(128,36,"FIT_TYPE_36")`.
- Directory enumeration always reads file index 0, with compression enabled. The resulting file starts with 16 bytes that the application does not inspect and therefore remain opaque. Every subsequent complete 16-byte record is `index:u16LE | dataType:u8 | identifier[3] | UNKNOWN_6:u8 | flags:u8 | size:u32LE | timestamp/value:u32LE`. The first identifier byte is exposed downstream as file subtype.
- The Android compatibility layer filters directory entries to those with READ and without ARCHIVE before presenting files to higher-level sync code.
- Message 5008 archives a file with `index:u16LE | 0x10`.
- Generic file index `0xFFFD` is the Garmin device XML file. This is distinct from directory index 0.
- Download request 5002 is sent before data. Status NOT_READY is retried once after a delay. A compressed read that the remote cancels is retried uncompressed by the static file manager.
- Incoming ordinary chunks use 5004. The receiver validates offset and rolling CRC, ACKs duplicate previous-offset chunks, reports offset mismatch, and aborts after too many consecutive invalid packets. CRC mismatch is fatal after the response is sent.
- Incoming compressed chunks use 5054. The first byte is a wrapping packet counter. Unexpected counter gets a three-byte response `receivedCounter,4,expectedCounter` and can continue. Other statuses are 0 success, 1 abort/internal malformed, 2 decompressed CRC mismatch, 3 DEFLATE failure. End-of-stream flag `0x02` finishes the current zlib stream and resets the inflater while cumulative output position/CRC continue.
- Message 5022 cancels an active transfer and is ACKed. If file data arrives when no read receiver exists, the app replies with an abort response instead of accepting bytes.

The 16-byte directory header and byte 6 of each directory record are deliberately left unresolved because the app itself does not interpret them on this path. Concrete file indices are dynamic directory data, not constants to guess.


## S-0017 — FIT subtype semantics needed for activity/health classification

`analysis/jadx/sources/kp2/EnumC31174ad.java` is the generated FIT file-type enum embedded in the package. It gives semantic names for several directory subtypes that matter to the owner's workflow: SETTINGS=2, SPORT=3, ACTIVITY=4, WORKOUT=5, COURSE=6, WEIGHT=9, BLOOD_PRESSURE=14, MONITORING_A=15, MONITORING_B=32, GOLF_SWING=36, GOLF_CLUB=37, SLEEP_DATA=49 and USER_BEHAVIOR_LOG=52.

Combined with S-0016's supported-file fallback `(dataType=128, subType=4, "FIT_TYPE_4")`, this supports treating data type 128 as the FIT type family and subtype 4 as ACTIVITY. The independent runtime exposes only names directly supported by the generated enum; manufacturer/reserved ranges with misleading decompiler labels are not assigned invented semantics.

The independent `fit.py` also validates the public FIT container header and reads the standard File ID global message 0 / field 0 to cross-check the file type inside a downloaded file. This gives two independent classification signals: directory subtype and file content. Real target-watch activity/health transfer remains dynamically unconfirmed until an actual directory/file can be read.

## S-0018 — GDI Smart protobuf transport and feature capabilities

Static analysis of `mq2/C35222d.java` plus its smali fallback resolves the generic GDI protobuf layer rather than relying on Garmin's generated classes at runtime.

- Messages 5043/5044 carry protobuf request/response chunks. Each chunk is `requestId:u16LE | offset:u32LE | totalLength:u32LE | chunkLength:u32LE | data`. The sender reserves 14 bytes from the current GFDI payload limit.
- Each chunk receives an eight-byte acknowledgement: request ID, offset, failure flag, and status. Statuses are 0 no-error, 100 unknown request, 101 duplicate, 102 missing, 103 exceeded length, 200 parse error, and 201 unknown protobuf message. Message 5045 carries a request ID and cancels a pending protobuf exchange.
- `GDISmartProto.Smart` has no ordinary fields; it is an extendable protobuf envelope. Core service is Smart extension field 13. GNCS service messages use Smart extension field 49.
- Inside Core service, Feature Capabilities Request/Response are fields 8/9 and Connection Ready Notification is field 14. Legacy configuration flag 95 gates the feature-capability exchange.
- Core Feature Capabilities Request fields are Garmin GUID bytes=1, client version=2, display name=3. The GNCS capability extension is field 12; the independent implementation does not fabricate the optional identity fields.
- GNCS capability request fields are notification-provider version=1, disabled-reason=2, default messaging app=3 and default dialer app=4. Garmin's semantic version encoding is `major<<16 | minor<<8 | patch`. The APK's own GNCS library happens to be 7.4.30, but the compatibility client advertises its own version rather than impersonating it.
- GNCS capability response fields are notification-client version=1 and supports-blocked-apps=2. Static behavior treats client version >=1 as supporting modified-after-added notifications.

Primary evidence: `analysis/jadx/sources/mq2/C35222d.java`, `analysis/apktool/smali_classes7/mq2/d.smali`, `GDISmartProto.java`, `GDICore.java`, `GDICoreExtension.java`, `GDIGNCS.java`, `GDIGNCSExtension.java`, `aq2/C2861i.java`, and `com/garmin/android/gncs/SmartNotificationsDataHandler.java`.

The runtime uses an independently written bounded protobuf-wire parser and explicitly modeled fields only; generated Garmin protobuf code is not imported or copied.
