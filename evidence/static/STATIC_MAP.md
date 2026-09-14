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

The outer GNCS transport is completed by S-0014, which resolves the ANCS-shaped payload, attribute/action codecs and optional XXTEA wrapper. Target-watch subscription order and supported user-visible actions remain device observations.

## S-0011 — Native-code boundary

Most GFDI framing/authentication remains managed-language, but next-generation FileAccess crosses JNI at Garmin MultiLink Reliable (MLR). `MLRInitializer` loads `libreliable-ml.so`; `MLRConnectionHelper` exposes native open/close, raw receive, ready-to-send and data-blob calls. The project reimplements the recovered wire behavior in Python and does not ship or load Garmin binaries. The static scanner includes `device/multilink`, `device/filetransfer`, `ui2`, `vi2` and `wi2` so this boundary cannot silently regress back to “no native protocol path.”

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
        +--> legacy file transfer (com.garmin.gfdi.file, gq2, hq2)
        +--> smart notifications/GNCS (com.garmin.android.gncs)
        +--> protobuf Core/FileAccess control (mq2, com.garmin.device.filetransfer)
                 |
                 +--> MultiLink GATT registration (vi2/wi2)
                         |
                         +--> MLR reliable packets (JNI in Garmin app; Python implementation in this repo)
```

This graph is the working map used by the implementation and protocol tests.

## S-0013 — Static-analysis completeness limits

JADX failures in complex coroutine methods are resolved with fallback output and Apktool smali before the result is recorded as a protocol fact. UUIDs without a watch-facing dataflow remain classified as unresolved rather than assigned a role. `CONFIRMED` is reserved for hardware observations; static findings use `SUPPORTED`.


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

- 5037 contains a one-byte visibility/mode value followed by a one-byte bitset length and that many download-category bytes. Values 0/1/2 map to manual, invisible and visible-as-needed; an unknown mode is logged and routed through the handler fallback branch.
- 5027 contains a one-byte bitset length followed by the same category bitset and is acknowledged with response data byte 0.
- 5009 has a 16-byte minimum fixed body: uint16 file index, one-byte data type, three-byte identifier, one preserved byte at offset 6 whose role is unresolved, one file-flag byte, uint32 file size and uint32 timestamp/value. The sync bridge treats the first identifier byte as file subtype.
- File-flag masks recovered from `com/garmin/gfdi/file/EnumC12038e.java` are READ `0x80`, WRITE `0x40`, ERASE `0x20`, ARCHIVE `0x10`, APPEND `0x08`, CRYPTO `0x04`.
- `analysis/jadx/sources/pv2/EnumC39930g.java` maps download-category bit positions to names used by the higher-level sync queue. Relevant examples include activities=5, software-update=8, device-settings=9, activity-summary=21, sleep=26, HRV-status=36 and uninstall-app=39. The complete recovered mapping is represented independently in `src/garmin_proto_lab/sync.py`.

This mapping identifies the generic category/request grammar. It does not identify which concrete 5009 file index/data-type/subtype represents activity or health data on the owner's watch; that remains a D-PLAN-0005 question.


## S-0016 — File directory enumeration and read-side transfer lifecycle

Static reconstruction of `com/garmin/gfdi/file/C12034a.java`, `gq2/C23676c.java`, `gq2/C23682i.java`, `gq2/C23674a.java`, `fq2/EnumC22108b.java` and `FileManagerCompat` establishes the complete generic read-side path without assuming target-specific file indices.

- Message 5007 applies a one-byte directory filter: 0 no-filter, 1 default, 3 pending-uploads-only. A response data byte of zero means the device supports the filter; nonzero means it does not.
- Message 5031 with empty request queries supported file types. Successful response byte 0 is an entry count followed by `dataType:u8 | subType:u8 | nameLength:u8 | name`. If the device explicitly returns UNKNOWN_OR_NOT_SUPPORTED, the app uses two legacy mappings: `(128,4,"FIT_TYPE_4")` and `(128,36,"FIT_TYPE_36")`.
- Directory enumeration always reads file index 0, with compression enabled. The resulting file starts with 16 bytes that the application does not inspect and therefore remain opaque. Every subsequent complete 16-byte record is `index:u16LE | dataType:u8 | identifier[3] | RESERVED_6:u8 | flags:u8 | size:u32LE | timestamp/value:u32LE`. The first identifier byte is exposed downstream as file subtype.
- The Android compatibility layer filters directory entries to those with READ and without ARCHIVE before presenting files to higher-level sync code.
- Message 5008 archives a file with `index:u16LE | 0x10`.
- Generic file index `0xFFFD` is the Garmin device XML file. This is distinct from directory index 0.
- Download request 5002 is sent before data. Status NOT_READY is retried once after a delay. A compressed read that the remote cancels is retried uncompressed by the static file manager.
- Incoming ordinary chunks use 5004. The receiver validates offset and rolling CRC, ACKs duplicate previous-offset chunks, reports offset mismatch, and aborts after too many consecutive invalid packets. CRC mismatch is fatal after the response is sent.
- Incoming compressed chunks use 5054. The first byte is a wrapping packet counter. Unexpected counter gets a three-byte response `receivedCounter,4,expectedCounter` and can continue. Other statuses are 0 success, 1 abort/internal malformed, 2 decompressed CRC mismatch, 3 DEFLATE failure. End-of-stream flag `0x02` finishes the current zlib stream and resets the inflater while cumulative output position/CRC continue.
- Message 5022 cancels an active transfer and is ACKed. If file data arrives when no read receiver exists, the app replies with an abort response instead of accepting bytes.

The 16-byte directory header is preserved as raw metadata. Byte 6 of each directory record is preserved as `reserved_6`; the recovered read path never branches on it. Concrete file indices are runtime directory data.


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


## S-0019 — Next-generation FileAccess control plane

Static protobuf descriptors plus `com/garmin/device/filetransfer/C11219a.java` / `C11220b.java` resolve the next-generation file API used when legacy configuration bit 90 is enabled.

- Smart extension field 43 carries FileAccess service messages; Feature Capabilities extension field 16 advertises FileAccess support. Garmin's client contributes an empty request capability rather than inventing optional server features.
- Item List request/response are service fields 9/10. Filters cover transaction/session IDs, flag UUIDs, included data types and modified-time metadata. Session IDs page a single listing; `next_transaction_id` supports later incremental listings.
- Pull request/response are fields 1/2. The only recovered transport enum is MultiLink Transport Pipe (0). A successful pull returns a transfer handle and optionally a compression window.
- Transfer Status request/response are fields 5/21. Completion is device-initiated: the host associates the request with the active transfer handle and replies only after the data path finishes. Unknown handles get an empty response.
- Cancel Transfer request/response are fields 18/19. Request field 1 is the transfer handle; SUCCESS and UNKNOWN_TRANSFER are both treated as successful/idempotent cleanup by Garmin's client.
- The higher upload/sync layer explicitly includes `FIT_TYPE_4` (activity), `FIT_TYPE_32` (monitoring) and `FIT_TYPE_49` (sleep) among recovered fitness-oriented data-type names. This is a filter/classification fact, not a guarantee that every watch exposes each type.

The independent runtime models the protobuf messages, pagination, pull negotiation and delayed transfer-status response without importing Garmin generated protobuf code.

## S-0020 — MultiLink registration and MLR reliable wire format

Static Java plus targeted Ghidra/objdump analysis of the x86_64 `libreliable-ml.so` split resolves the FileAccess transport data plane.

- MultiLink service UUID is `6A4E2800-667B-11E3-949A-0800200C9A66`. Data characteristic candidates are `6A4E2810..2819`; each maps to `6A4E2820..2829` for the paired write characteristic when present, otherwise the same characteristic is used in both directions.
- Register command is `00 00 | clientId:u64LE | serviceId:u16LE | flags:u8`; flag `0x02` requests reliable service. Successful response command `01` returns handle, reliable flag and optional revision. Status 3 can return an alternate characteristic. Registration service ID is 4.
- With registration-service revision >=1, `C48933f0` prepends the registration handle and sends MultiLink info-page requests. Page 0 returns a bitset of low logical service IDs; `AbstractC48943o.m68643b` expands each set bit to its integer service ID. Page 5 carries `serviceId:u16LE` and returns status + revision. `C36128e` uses the resulting service list to decide whether GFDI service 1 is available.
- FileAccess transport-pipe service IDs are `0x2018,0x4018,0x6018,0x8018,0xA018,0xC018,0xE018`. Pipe configure is `00 | direction:u8 | transferHandle:u64LE`, direction 0 read / 1 write. Smali fallback resolves configure general statuses 0 success / 1 unknown command / 2 invalid command data, and configure statuses 0 success / 1 unknown transfer handle / 2 unexpected direction / 3 unexpected size.
- Non-reliable MultiLink packets use a one-byte handle. Reliable MLR packets use two bytes: `b0=0x80|((handle&7)<<4)|(RN>>2)`, `b1=((RN&3)<<6)|SN`, with six-bit cumulative ACK/request number RN and six-bit sequence number SN. Payload capacity is `max_write_length-2`.
- Normal connections use 64 sequence values. Native mode `0xFF` exposes an eight-value diagnostic sequence space; other nonzero modes return `0xFF` from the format helper.
- Initial send window is 32 packets; an advancing ACK grows it by one through 63. A retransmission timeout halves it through a floor of one.
- Receive ACK policy is five data packets or a 10 ms deferred-ACK timer. Any outbound reliable packet carries the current RN and clears the pending standalone ACK.
- Initial RTO is 1000 ms. RTT updates use alpha `1/8`, beta `1/4`, and `RTO=sRTT+max(0.5*sRTT,4*RTTVAR)` with a 500 ms floor. Timeout doubles RTO through 20,000 ms.
- Timeout resets the send sequence to the unacknowledged base and retransmits within the reduced window.
- Four native formatter self-tests provide exact wire vectors used by `tests/test_mlr.py`: `05 01`, `d0 c2 ff`, `92 cd ff de a2`, `ff ff 01 02 03`.

Primary evidence: `vi2/C48927c0.java`, `C48939k.java`, `C48946r.java`, `wi2/C50817d.java`, `ui2/C47398e.java`, `MLRConnectionHelper.java`, and local decompilation of `mlr_format_*`, `MLR_reliable_*`, `FUN_0012cac0` and `FUN_0012d050`.

## S-0021 — Fresh pairing bonds; saved Garmin auth reconnect skips a new bond request

`o72/C37070a.m55705b()` returns true when any saved Garmin authentication field is present: LTK (16 bytes), EDIV (2 bytes), or RAND (8 bytes). It is therefore a saved-auth-material predicate, not a fresh-pairing capability bit.

`w72/C50242e.m69878c` uses that predicate as follows:

- without saved LTK/EDIV/RAND, it forwards the caller's `forceSystemBonding` value to `m69881f`;
- with saved auth material, it registers the record in `AuthRegistry` and calls `m69881f(..., false)`.

`m69881f` logs the value as `requiresBond` and passes it to the BLE pairing connection. The fresh pairing strategy in `zj2/C55757b.m75743o` creates `C37070a(address, true)` with no LTK/EDIV/RAND and calls `m69878c(..., true)`, so first-time pairing requests an Android Bluetooth bond. The saved-auth reconnect entry point `rc0/C42268o2.mo61248p` supplies LTK/EDIV/RAND and calls `m69878c(..., false)`, so persistent GFDI reconnect does not request another bond.

`yg2/C54129v` owns the Android `BluetoothDevice.createBond()` state and a 45-second bond timeout. The higher pairing strategy starts a 90-second handshake timer and retries a failed bond once (`f213081i < 1`); its two `m75744p` call sites pass `false`, so this path does not remove an existing bond before retrying. The implementation exposes both routes with a 45-second per-attempt bond timeout and two attempts. With no Garmin-auth record, `requires_system_bond()` selects the system-bond route; the BLE backend checks BlueZ paired state before calling Pair, so an existing OS bond is reused. A persisted Garmin-auth record selects the unbonded 5102 route. `--system-bond` and `--garmin-auth` force either route for controlled comparison. Raw probe mode remains unbonded by default.

`AuthRegistry.registerAuthInfo(mac, ltk, ediv, rand)` stores the three values and invokes callbacks in semantic order `(mac, ediv, rand, ltk)`. `DefaultAuthStateCallback` broadcasts `EXTRA_LONG_TERM_KEY`, `EXTRA_ENCRYPTED_DIVERSIFIER`, and `EXTRA_RANDOM_NUMBER`. `oj2/C37533b.onDeviceAuthenticated` writes the same values into worker keys `GBLE_LTK_KEY`, `GBLE_DIV_KEY`, and `GBLE_RAND_KEY`. No session key, SKD, IV, STK, or packet counter is persisted by this callback path.

## S-0022 — Authentication reserved bytes are non-branching

Dataflow through `bq2/C4565c.java` resolves fields previously labeled unknown:

- 5103 reads timeout bytes 1–2 and mode byte 5; bytes 3–4 never participate in a branch or response and are preserved as reserved.
- 5107 decrypts 32 bytes and consumes LTK 0–15, EDIV 16–17 and RAND 18–25; bytes 26–31 are reserved.
- 5109 decrypts bytes 1–24 and compares only plaintext 0–15 with the derived session key; plaintext 16–23 is reserved.
- 5111 decrypts 16 bytes, consumes byte 0 and device IV bytes 8–11, and ignores/reserves bytes 1–7 and 12–15. The response copies byte 0, leaves bytes 1–7 zero, generates host IV bytes 8–11 and random bytes 12–15.
- The AuthManager constructor registers inbound handlers only for 5101, 5103, 5107, 5108, 5109, 5111 and 5112. A full source search finds numeric ID 5110 only in the message-name map, so Passkey Redisplay has no sender/receiver call site in this build.

These bytes do not control the required pairing/reconnect workflow.

## S-0023 — Complete FileAccess service schema and checksum method

The generated `GDIFileAccess` descriptor defines service fields 1–26. The implementation covers pull/push, Transfer Status, Priority Update, Item List/cancel, Item Added, Delete Item, Modify Flags, Item Updated, Cancel Transfer, Resource Update, Sync Button, software-update part-number request/response and item checksum request/response.

`fi2/C21878m.java` computes `TRUNCATED_MD5` by hashing the complete clear file with MD5, taking digest bytes 0–7 and interpreting them with a little-endian `ByteBuffer.getLong()`. FileAccess checksum result values are UNSPECIFIED=0, SUCCESS=1, item-missing=2, size-too-large=3, internal-error=4 and device-busy=5.

`src/garmin_proto_lab/file_access_proto.py` and `tests/test_file_access_proto.py` implement and test these layouts without generated protobuf classes.

## S-0024 — Fitness persistence and integrity chain

A completed fitness object has independent integrity checks at multiple layers:

1. MLR sequence/RN state rejects out-of-order delivery and drives retransmission.
2. FileAccess Transfer Status closes the negotiated transfer handle lifecycle.
3. FileAccess truncated-MD5 is checked when advertised by the watch.
4. FIT parsing validates signature, header CRC, File ID type and trailing full-file CRC.

`NextGenFitnessSync` writes clear bytes to a mode-`0600` `.part` file during transfer, resumes at the saved clear-byte offset, validates the completed object, and atomically publishes the `.fit` file. Tests cover sequence wrap beyond 64 packets, out-of-order suppression, timeout/window behavior, byte-offset resume, checksum mismatch and persisted-prefix resume.

## S-0025 — GFDI service 1 fallback over MultiLink

`ne2/C36128e` is the MultiLink subscriber. After obtaining the device MultiLink service list it checks service ID `1` explicitly. When service `1` is supported and no dedicated GFDI service intersects the configured dedicated-service set, it creates a `C36126c` shim for logical Connect Mobile GFDI service `9B012401-BC30-CE9A-E111-0F67E491ABDE` with the ordinary GFDI read/write characteristic UUIDs. If a dedicated GFDI service exists, the app logs that GFDI-over-MultiLink is disabled.

`C36126c.m54617g` maps logical Connect Mobile GFDI service directly to MultiLink service ID `1`. Its subscription path calls `C48939k.m68639m(1, reliable)`; `reliable` is true when the MLR engine is loaded for that GFDI route. The shim's write method sends GFDI bytes through the registered MultiLink service and its callback returns received service data to the normal GFDI subscriber. For reliable mode, the shim reports a larger logical write capacity while the physical MultiLink packets still use the underlying ATT payload minus the two-byte MLR header.

This resolves the physical transport fallback used by watches that expose MultiLink data characteristics without a dedicated GFDI pair. `src/garmin_proto_lab/transport.py` now selects direct GFDI first and automatically registers MultiLink service `1` otherwise; `src/garmin_proto_lab/multilink_gfdi.py` carries the same GFDI COBS byte stream over reliable or non-reliable MultiLink.

## S-0026 — MultiLink client identity is Garmin Connect client_uuid 0x01

`assets/client_config.xml` declares `<client_uuid>0x01</client_uuid>`. `GdiClientConfigurationUtil.fromClientConfig()` reads this field as a long and stores it in `C37074e.f141066c`. `ne2/C36128e.initialize()` passes that exact value into `new C48939k(gatt, clientUuid)`, and the MultiLink registration helper writes it as the uint64 little-endian `connection_id` in register/close commands. The default compatibility client therefore uses MultiLink client ID `0x01`; the CLI override exists for diagnostics.

## S-0027 — Bonded devices use StubAuth; proprietary Garmin auth is the unbonded route

`DefaultAuthDelegate.isGarminAuthAllowed(connectionId)` resolves the auth fork at GFDI startup. It obtains the Android `BluetoothDevice` and returns true only when `Build.MANUFACTURER` uppercases to `VIVO` or the remote device bond state is not `BOND_BONDED` (12).

`aq2/C2861i` calls this predicate before opening the GFDI handshake. If true, it installs `bq2/C4565c` AuthHandler, which implements messages 5101..5112. If false, it installs `bq2/C4587y` StubAuthHandler. StubAuth registers message 5101, completes its auth future with `C4586x(null,null,null)` when started, and throws `GarminAuthNotAllowedException` if a 5101 message is actually received.

Combined with S-0021, the normal fresh pairing route is therefore: request OS bond -> enter GFDI while bonded -> StubAuth/no proprietary LTK. The separate unbonded route runs Garmin auth and persists LTK/EDIV/RAND; saved Garmin-auth reconnect deliberately sets `requiresBond=false`. The compatibility CLI now exposes both routes and accepts system-bond completion without waiting for a 5101 sequence.

## S-0028 — Host Configuration combines app capabilities with handler bits

`zi0/C55704f.mo5977j()` converts every `SupportedCapability` loaded from Garmin Connect's `assets/client_config.xml` to its enum ordinal. `aq2/C2850a.mo5977j()` unions that app set with configuration sets from active GFDI and protobuf handlers before the host answers Configuration 5050.

For the direct pairing/fitness workflows, recovered app ordinals are Connect Mobile FIT Link 0, Sync 3, Device Initiates Sync 4, Host Initiated Sync Requests 5, GNCS 6, Explicit Archive 29, Request Pair Flow 64 and Current Time Request Support 71. `FileTransferManager.getConfiguration()` returns singleton `{90}`; ordinal 90 is `SYNC_2` in the app capability enum and enables the newer FileAccess manager. `aq2/C2861i` tests peer configuration bit 95 before sending Core FeatureCapabilities, but 95 is outside this build's `SupportedCapability` enum and is a peer protocol gate rather than a base app capability.

`src/garmin_proto_lab/capabilities.py` defines the pairing, fitness and notification subsets used by the reference client instead of advertising unrelated app features that the compatibility layer does not implement.


## S-0029 — Standard FIT wellness profile fields

The public Garmin FIT Python SDK profile at revision `f0d86b18195dbdf9c5b2f135aad5d6ae541f5fbb` reports FIT Profile `21.214.0`. It is used here only as a public protocol-profile cross-reference; the compatibility runtime does not import or depend on that SDK.

For the fitness files already identified by the watch-facing protocol, the standard profile resolves semantic fields needed for local fitness tracking: WEIGHT_SCALE (30) covers body weight/composition, BLOOD_PRESSURE (51) covers pressure/heart-rate/status, MONITORING (55) includes distance, cycles/steps, active time, activity type/subtype, calories, heart rate, intensity, ascent/descent and intensity minutes; HRV (78) carries RR intervals; MONITORING_INFO (103) carries activity calibration and resting metabolic rate; HR (132) carries filtered BPM/event timestamps; MONITORING_HR_DATA (211) carries resting heart-rate values; STRESS_LEVEL (227) carries stress value/time; SPO2_DATA (269) carries SpO2/confidence/mode; SLEEP_LEVEL (275) carries awake/light/deep/REM state; RESPIRATION_RATE (297) carries breaths/minute; HSA_ACCELEROMETER_DATA (302), HSA_STEP_DATA (304), HSA_SPO2_DATA (305), HSA_STRESS_DATA (306), HSA_RESPIRATION_DATA (307), HSA_HEART_RATE_DATA (308), and HSA_BODY_BATTERY_DATA (314) define interval-based sensor arrays for HSA files, including acceleration, steps, SpO2, stress, respiration, heart rate, and Body Battery.

`src/garmin_proto_lab/fit.py` maps these standard field numbers, scales and enums after FIT container validation while retaining every raw numeric field for forward compatibility. The mapping is covered by T-0080. Session (18), Lap (19), and Activity (34) aggregates are also projected with standard timer/distance/calorie/speed/heart-rate/cadence/power/ascent/descent/training-effect fields and sport/sub-sport names (T-0082). These mappings add no Garmin runtime dependency.


## S-0030 — FileAccess is selected by peer Configuration bit 90

`li2/C33523c.m51997m()` returns exactly `peer.getConfigurationFlags().contains(90)`. `zi0/C55704f.mo5970a()` creates/adds the next-generation `FileTransferManager` only when that predicate is true and tears down an existing Sync2 manager when it becomes false. This makes watch Configuration bit 90 the runtime FileAccess/Sync2 selector.

Peer Configuration bit 95 has a different role: `aq2/C2861i` uses it only to decide whether to send the Smart/Core FeatureCapabilities request. `FileTransferManager.start()` still starts its protobuf handler independently of an optional FileAccess Capabilities extension; `receiveCapabilities()` merely stores that extension, and checksum support reads it when present. Therefore a watch advertising bit 90 without bit 95 can still use FileAccess, but optional server checksum/custom-flag capability data is unavailable.

The compatibility client now selects FileAccess from peer bit 90, uses bit 95 only for the optional capability query, and treats a missing FileAccess capability extension as absence of optional capability metadata rather than absence of the FileAccess transport itself.


## S-0031 — Extended Garmin fitness-file families and wellness messages

Gadgetbridge revision `a0948ee1cbc2a870f91d313f8e37df5f524465f7` provides a public interoperability cross-reference for Garmin files beyond the standard FIT profile. Its Garmin file map identifies data type 128 subtypes 44 (metrics), 68 (HRV status), 70 (HSA), and 73 (skin temperature), in addition to activity 4, monitoring 32, and sleep 49. Garmin Connect independently names `FIT_TYPE_44` as BIOMETRIC in `li2/C33523c`, while the remaining subtype numbers sit in manufacturer ranges in the bundled FIT enum.

The same Gadgetbridge revision identifies Garmin-specific FIT messages used by these files: PHYSIOLOGICAL_METRICS 140; SLEEP_DATA_INFO 273; SLEEP_DATA_RAW 274; SLEEP_STATS 346; HRV_SUMMARY 370; HRV_VALUE 371; SKIN_TEMP_RAW 397; and SKIN_TEMP_OVERNIGHT 398. Its monitoring decoder also reconstructs field-26 `timestamp16` against the previous monitoring timestamp and derives activity type/intensity from packed field 24 when the normal fields are absent.

`fit.py` now retains these file families during sync, names the Garmin-specific messages, projects their established fields, includes stress/body-energy field 3, reconstructs monitoring `timestamp16`, and preserves every raw record/field beside the semantic projection. No Gadgetbridge runtime code or library is loaded.
