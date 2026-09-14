# Garmin Watch Protocol Specification

This document contains independently stated interoperability facts inferred from the lawfully acquired Garmin Connect 5.29 Android package and validated by independent code/tests where possible. It intentionally does **not** reproduce Garmin source code. Decompiled output remains local evidence under `analysis/`; runtime code is written from this document.

## Evidence and confidence

Evidence IDs:

- Static evidence: `S-####` in `evidence/static/STATIC_MAP.md`
- Dynamic observation/capture: `D-####` (none yet; watch unavailable)
- Protocol fact: `P-####` (this file)
- Automated test: `T-####` (test mapping below)

Confidence:

- `CONFIRMED`: independently reproduced and verified by a direct test/observation appropriate to the fact.
- `SUPPORTED`: strong static evidence and/or independent unit verification; watch confirmation remains.
- `HYPOTHESIS`: plausible interpretation requiring a targeted experiment.
- `UNKNOWN`: observed field/behavior with no asserted meaning.

Unless explicitly labelled otherwise, facts below are `SUPPORTED` while no watch is available.

---

## Layer 0 — Android BLE transport

### P-0001 — Connection lifecycle (`SUPPORTED`, S-0004)

The BLE lifecycle recovered from the app is:

`NOT_STARTED -> SCANNING -> CONNECTING_GATT -> WAITING_FOR_BOND -> DISCOVERING_SERVICES -> AVAILABLE -> DISCONNECTING -> FINISHED`.

Not every connection traverses `WAITING_FOR_BOND`; it is conditional on bond state. Android BLE scan and `connectGatt(..., TRANSPORT_LE)` are used, followed by service discovery.

### P-0002 — ATT write sizing (`SUPPORTED`, S-0004; T-0001)

The GFDI byte stream is written as consecutive characteristic-write payloads. The sender asks the BLE abstraction for the current maximum characteristic payload and slices the stream accordingly. There is no Garmin-specific header at each ATT fragment.

The implementation therefore treats ATT fragmentation as transport slicing only:

`stream bytes -> chunks <= negotiated characteristic payload -> writes`

and reassembly as byte concatenation.

### P-0003 — Notification stream (`SUPPORTED`, S-0004; T-0002)

Incoming characteristic-change payloads are concatenated directly into a byte stream. A GFDI/COBS packet may begin in one notification and end in another; ATT notification boundaries must not be treated as protocol boundaries.

### P-0004 — MTU behavior (`SUPPORTED`, S-0004)

Garmin Connect requests ATT MTU 515. The effective characteristic payload size exposed by the BLE layer is negotiated MTU minus 3 bytes, with 20 bytes as the legacy/default payload size before larger negotiation.

Android 14+ may normalize the first GATT MTU request at the OS level; compatibility code must accept the negotiated callback value rather than assuming exactly 515.

### P-0005 — GATT Service Changed (`SUPPORTED`, S-0004)

The generic BLE layer recognizes the Bluetooth SIG Generic Attribute service UUID `00001801-0000-1000-8000-00805F9B34FB` and Service Changed characteristic `00002A05-0000-1000-8000-00805F9B34FB` and may subscribe while establishing a usable connection.

---

### P-0006 — Independent transport lifecycle and failure boundary (`SUPPORTED`, S-0004/S-0005; T-0003/T-0004)

The compatibility transport keeps BLE operations behind a platform-neutral backend and exposes the recovered lifecycle as explicit semantic state. A connection is usable only after bonding when required, service discovery, selection of a complete GFDI read/write characteristic pair, MTU negotiation and notification subscription. The dedicated Connect Mobile service is preferred; a generic GFDI pair is accepted only when both read/notify and write characteristics occur in the same discovered service.

Transport writes are serialized as slices no larger than `negotiated_mtu - 3`; notification payloads are delivered in order as byte-stream chunks. Setup failures transition to a failed state and trigger disconnect cleanup rather than leaving a partially usable link. This is an offline implementation rule derived from P-0001..P-0005; target-watch service selection remains to be confirmed by D-PLAN-0001.

---

## Layer 1 — Garmin BLE services

### P-0010 — Connect Mobile service UUIDs (`SUPPORTED`, S-0005)

Dedicated Connect Mobile GFDI service:

| Role | UUID |
|---|---|
| Service | `9B012401-BC30-CE9A-E111-0F67E491ABDE` |
| Host write | `DF334C80-E6A7-D082-274D-78FC66F85E16` |
| Host read/notify | `4ACBCD28-7425-868E-F447-915C8F00D0CB` |

The direction names are from the Android host's perspective.

### P-0011 — Generic GFDI characteristic UUIDs (`SUPPORTED`, S-0005)

The generic Garmin GFDI namespace includes:

- host write: `6A4E4C80-667B-11E3-949A-0800200C9A66`
- host read/notify: `6A4ECD28-667B-11E3-949A-0800200C9A66`

Which service/characteristic pair a particular watch exposes must be determined by GATT discovery, not hard-coded solely from the app registry.

### P-0012 — Realtime service family (`SUPPORTED`, S-0005)

Realtime service: `6A4E2500-667B-11E3-949A-0800200C9A66`.

Observed characteristic family:

| Meaning | UUID suffix |
|---|---|
| Heart rate | `2501` |
| Steps | `2502` |
| Calories | `2503` |
| Ascent | `2504` |
| Intensity minutes | `2505` |
| HRV | `2507` |
| Stress | `2508` |
| Accelerometer | `2509` |
| SpO2 | `250C` |
| Body Battery | `250D` |

All use the Garmin base suffix `-667B-11E3-949A-0800200C9A66`. Payload grammars remain outside the v1 required workflow unless needed by the target watch.

### P-0013 — MultiLink UUIDs (`SUPPORTED`, S-0005/S-0020)

MultiLink service is `6A4E2800-667B-11E3-949A-0800200C9A66`. Data characteristic candidates are `6A4E2810..2819`; each primary maps to `6A4E2820..2829` for the paired write characteristic when that characteristic is present. Registration/control and MLR framing are specified in P-0701..P-0703.

### P-0014 — GFDI over MultiLink service 1 (`SUPPORTED`, S-0025; T-0075)

Garmin supports two physical routes for the same logical GFDI service. If a dedicated GFDI service/characteristic pair exists, it is used directly. Otherwise the MultiLink subscriber registers logical service ID `1` and exposes the normal GFDI read/write UUIDs to the upper GFDI stack through a shim. With the reliable engine available, registration requests MLR; without it, the same service can use a one-byte non-reliable MultiLink handle.

The compatibility transport follows the same route selection: direct GFDI first, then MultiLink service `1`. A reliable GFDI handle uses P-0702/P-0703 MLR and forwards each accepted payload fragment into the ordinary COBS byte-stream decoder. A non-reliable handle uses `handle:u8 | GFDI stream bytes`. This route is transparent to the frame/authentication/semantic layers.

---

## Layer 2 — COBS stream framing

### P-0100 — Packet delimiters (`SUPPORTED`, S-0006; T-0010)

Each plaintext or secure GFDI packet is COBS encoded and placed on the byte stream as:

```text
00 | COBS(encoded bytes) | 00
```

The receiver scans until a zero, then accumulates bytes until the next zero. Repeated zero delimiters are harmless synchronization points.

### P-0101 — COBS algorithm (`CONFIRMED`, S-0006; T-0010/T-0011)

Encoding is standard Consistent Overhead Byte Stuffing. Independent tests include known vectors and property-based arbitrary-byte round trips. This fact concerns the generic COBS transform itself; use of COBS by a specific watch remains to be dynamically observed.

### P-0102 — Encoded-packet bound (`SUPPORTED`, S-0006)

The recovered reader initializes a 16 KiB encoded-packet buffer. The implementation applies a finite frame bound and rejects overlength frames before allocation growth.

---

## Layer 3 — Plaintext GFDI frame

### P-0110 — Ordinary frame grammar (`SUPPORTED`, S-0006; T-0020)

Decoded, pre-COBS ordinary frame:

```text
offset  size  field
0       2     total frame length, uint16 little-endian
2       2     message ID, uint16 little-endian; bit 15 must be clear
4       N     payload
4+N     2     CRC16, little-endian
```

Total length includes the two CRC bytes. Minimum frame length is 6 bytes.

Because bit 15 of the second message-type byte is the transaction marker, ordinary message IDs are represented in the 0..0x7FFF range.

### P-0111 — Transaction frame grammar (`SUPPORTED`, S-0006; T-0021)

Compact transaction mode replaces the two-byte ordinary message field:

```text
offset  size  field
0       2     total frame length, uint16 little-endian
2       1     compact message ID = full message ID - 5000
3       1     bit7=1, low 5 bits = transaction ID (0..31)
4       N     payload
4+N     2     CRC16 little-endian
```

Thus compact transaction framing directly represents message IDs 5000..5255 and transaction IDs 0..31. Transaction IDs cycle in that five-bit range.

### P-0112 — CRC (`CONFIRMED`, S-0006; T-0022)

Frame CRC is reflected CRC-16 with polynomial `0xA001`, initial value zero, no final XOR, serialized little-endian. This is CRC-16/ARC behavior for the parameters used here. Independent implementation passes the canonical `123456789 -> 0xBB3D` check.

CRC covers every raw frame byte before the CRC itself, including the length and message/transaction header.

### P-0113 — Acknowledgement/response (`SUPPORTED`, S-0006; T-0023)

Message ID 5000 is the acknowledgement envelope. Its payload begins:

```text
uint16 LE  original request message ID
uint8      response status
bytes      response-specific data
```

If transaction mode is active, the 5000 response carries the matching transaction ID in the compact frame header.

Known response status byte values:

| Value | Meaning |
|---:|---|
| 0 | ACK |
| 1 | NAK |
| 2 | UNKNOWN_OR_NOT_SUPPORTED |
| 3 | COBS_DECODER_ERROR |
| 4 | CRC_ERROR |
| 5 | LENGTH_ERROR |

Unexpected status values must be preserved as unknown rather than silently remapped.

### P-0114 — Dispatcher timeout and unknown handling (`SUPPORTED`, S-0006)

The static dispatcher has a default request timeout of approximately 45 seconds. Unhandled requests can receive `UNKNOWN_OR_NOT_SUPPORTED`; response matching is message/transaction based.

---

### P-0115 — Async request/link behavior (`SUPPORTED`, S-0006; T-0024)

The independent message link allocates one of 32 five-bit transaction IDs, encodes the request through the GFDI/COBS stack, and resolves only an acknowledgement whose transaction ID and acknowledged message type both match the pending request. A timeout or caller cancellation removes the pending transaction; a non-ACK response is surfaced as a semantic rejection rather than treated as success. Unknown inbound messages and unmatched acknowledgements are preserved as observable events/problems.

---

## Layer 4 — Authentication and secure session

### P-0200 — Active cipher (`SUPPORTED`, S-0007; T-0030)

The active authentication algorithm in this build is XXTEA. The static enum contains AES-128, but the active supported-algorithm array contains XXTEA only; the implementation advertises XXTEA only.

XXTEA uses 32-bit little-endian words, a 16-byte key and delta `0x9E3779B9`. The independent implementation was cross-checked against the published Wheeler/Needham `btea` formulation compiled with `uint32_t`.

### P-0201 — Authentication message family (`SUPPORTED`, S-0007)

| ID | Meaning |
|---:|---|
| 5101 | Auth Negotiation Begin |
| 5102 | LTK Reconnect |
| 5103 | STK Begin Generation |
| 5104 | Confirm Number |
| 5105 | STK Random Number |
| 5106 | STK Generation Status |
| 5107 | LTK Key Distribution |
| 5108 | Session Key SKD Distribution |
| 5109 | Session Key Verification |
| 5110 | Passkey Redisplay |
| 5111 | Secure Session |
| 5112 | Out-of-Band Passkey Data |

In Garmin Connect 5.29, the authentication manager registers inbound handlers for 5101, 5103, 5107, 5108, 5109, 5111 and 5112. Messages 5102/5104/5105/5106 are host-initiated requests whose replies are correlated by the request layer. ID 5110 exists only in the message-name mapping: a full source search contains no sender or receiver call site for Passkey Redisplay in this build, so it is not part of the implemented pairing state machine.

### P-0202 — 5101 negotiation (`SUPPORTED`, S-0007)

Incoming 5101 requires at least 5 bytes. Bytes 1..4 carry the peer algorithm bit mask. The host response data conveys:

1. algorithm compatibility status,
2. whether saved long-term material exists,
3. host supported-algorithm bit mask (uint32 LE).

When persistent long-term key material, encrypted diversifier and random number are all present, the reconnect path is attempted with message 5102.

### P-0203 — Passkey modes and OOB distribution (`SUPPORTED`, S-0007; T-0039)

5103 contains a pairing timeout in bytes 1–2, reserved bytes 3–4, and passkey mode in byte 5. Recovered mode values:

- 0: visible/user-entered passkey,
- 1: "just works" using a 16-byte all-zero passkey,
- 2: out-of-band mode.

The passkey/key material consumed by the confirm transform is exactly 16 bytes. A zero timeout is normalized to 30 seconds in the app path. For the visible mode, the user-entered decimal passkey is parsed as a signed 32-bit integer, serialized little-endian as four bytes, then zero-padded to 16 bytes before the confirm/STK transform.

Out-of-band key distribution also has a separate 5112 handler in the recovered stack. It requires at least 16 bytes. On a non-dual-pairing device it ACKs with response byte `1`; on a dual-pairing device it ACKs `0` and stores exactly the first 16 bytes as an OOB passkey associated with the counterpart Bluetooth identity. The independent engine gates 5112 the same way and can use a received OOB key when mode 2 is later requested.

### P-0204 — MAC block (`SUPPORTED`, S-0007; T-0031)

For the confirm transform, the Bluetooth MAC string is parsed into six octets, reversed, and placed in bytes 0..5 of a 16-byte zero-filled block.

### P-0205 — Confirm value (`SUPPORTED`, S-0007; T-0032)

Given 16-byte passkey `K`, 16-byte random `R`, and 16-byte reversed-MAC block `M`:

```text
A = XXTEA_encrypt(R, K)
B = A XOR M XOR R
confirm = XXTEA_encrypt(B, K)
```

The host sends its confirm with 5104 and later its random with 5105. The peer random is independently confirmed with the same transform.

### P-0206 — Short-term key (`SUPPORTED`, S-0007; T-0033)

After both random values are confirmed:

```text
seed = host_random[0:8] || device_random[0:8]
STK = XXTEA_encrypt(seed, passkey)
```

5106 communicates success/failure of STK establishment.

### P-0207 — Long-term-key distribution (`SUPPORTED`, S-0007)

5107 carries an encrypted 32-byte block. Decrypt using STK. Recovered meaningful layout:

```text
0..15   long-term key (LTK)
16..17  encrypted diversifier (EDIV)
18..25  random number (RAND)
26..31  padding/reserved
```

On success the app persists LTK/EDIV/RAND through its authentication delegate. Persistent storage format is an application concern; the compatibility layer must store these values securely and scoped to the paired device.

### P-0208 — Session-key derivation (`SUPPORTED`, S-0007; T-0034)

5108 exchanges two eight-byte SKD contributions. The static host path forms:

```text
SKD = device_skd[0:8] || host_skd[0:8]
session_key = XXTEA_encrypt(SKD, LTK)
```

Host SKD bytes are generated randomly. 5109 verifies the resulting session key through an encrypted verification block.

### P-0209 — Secure-session initialization (`SUPPORTED`, S-0007)

5111 establishes per-direction initialization values. The decrypted 16-byte request uses byte 0 as the selector and bytes 8–11 as the device IV; bytes 1–7 and 12–15 are reserved. The response copies byte 0, leaves bytes 1–7 zero, generates host IV bytes 8–11 and random bytes 12–15, encrypts that block with the session key, and sends it in the plaintext GFDI acknowledgement. Secure outer wrapping begins only after that acknowledgement is written.

### P-0210 — Outbound secure packet (`SUPPORTED`, S-0007; T-0035)

After secure session activation, an inner raw GFDI frame is wrapped as follows before COBS:

```text
plaintext body:
  uint32 LE packet_counter
  4 bytes host direction IV/diversifier
  inner GFDI frame
  zero padding to a 4-byte word multiple

ciphertext = XXTEA_encrypt(plaintext body, session_key)
outer packet = uint16 LE (2 + len(ciphertext)) || ciphertext
```

The host counter starts at zero and increments per outbound secure packet.

The static padding formula always adds `4 - (used_length mod 4)` bytes, so when already aligned it adds four zero bytes rather than zero.

### P-0211 — Inbound secure packet validation (`SUPPORTED`, S-0007; T-0036)

Inbound secure packets:

1. validate the outer uint16 length,
2. decrypt the ciphertext with the session key,
3. read uint32 LE packet counter,
4. reject counters lower than the last accepted value,
5. require bytes 4..7 to match the device direction IV/diversifier,
6. read the inner GFDI length at decrypted offset 8,
7. return exactly that inner frame for ordinary GFDI validation.

Equal counters are accepted by the recovered comparison (`<`, not `<=`); the implementation matches that policy.

---

### P-0212 — Session state and persistence boundary (`SUPPORTED`, S-0007; T-0037/T-0038)

The offline state model separates persistent pairing material from per-connection secure-session material. Only LTK (16 bytes), EDIV (2 bytes) and RAND (8 bytes) are persistent. Session key, host/device SKD values, secure-session IV/diversifier values and packet counters are regenerated for a new secure connection and are not part of the persistent record.

A fresh authentication path is modeled as negotiation -> passkey-mode selection -> confirm/random exchange -> STK established -> LTK distribution -> SKD exchange -> session verification -> secure-session initialization. A saved LTK record causes negotiation to attempt 5102 reconnect before falling back to fresh authentication. Visible/OOB passkey waits and the general authentication path are bounded by timeouts; unsupported passkey modes, wrong-state messages, confirm mismatch and session-verification mismatch fail or restart explicitly.

State/transition table used by the independent engine:

| State | Input/action | Guard / validation | Output/action | Next state | Timeout / failure |
|---|---|---|---|---|---|
| disconnected/failed | start | local | none | waiting negotiation | 30 s auth deadline |
| waiting negotiation | watch 5101 | >=5 bytes, XXTEA bit common | ACK negotiated algorithm + persistent-key-present flag | reconnecting LTK when LTK/EDIV/RAND exist, else waiting STK begin | no common algorithm => failed |
| reconnecting LTK | host 5102 | saved EDIV2+RAND8 | request LTK reconnect | waiting SKD on status 0 | nonzero => host 5101 restart / fresh auth |
| waiting STK begin | watch 5103 | >=6 bytes; mode 0/1/2 | ACK mode/status | visible/OOB => waiting passkey; just-works => STK exchange | unsupported mode => failed |
| waiting passkey | application passkey | exactly 16 key bytes after decimal conversion; visible timeout supplied by watch, zero => 30 s; OOB default 30 s | begin STK exchange | STK exchange | timeout => failed |
| STK exchange | host 5104 | generate host random16 and confirm | receive peer confirm16 | STK exchange | bad status/length => failed |
| STK exchange | host 5105 | send host random16 | receive peer random16; recompute peer confirm | STK exchange | status 2 / confirm mismatch => 5106 fail then restart |
| STK exchange | host 5106 | confirm matched | status 0 | waiting LTK | mismatch sends status1 and restarts |
| waiting LTK | watch 5107 | >=33 bytes, STK available, decrypt 32 | persist LTK16+EDIV2+RAND8; ACK status0 | waiting SKD | no STK/decrypt failure => ACK failure/failed |
| waiting SKD | watch 5108 | >=8 bytes, persistent LTK exists | generate host SKD8, derive 16-byte session key, ACK status0+host SKD8 | waiting verification | no LTK => ACK status1/failed |
| waiting verification | watch 5109 | >=25 bytes, session key exists | decrypt 24, compare first16 to session key, ACK status0/2 | waiting secure session on match | mismatch => restart policy |
| waiting secure session | watch 5111 | exactly/at least 16 encrypted bytes, session key exists | decrypt peer initialization; ACK encrypted host initialization while outer GFDI is still plaintext | established | malformed/no key => failed |
| established | subsequent traffic | secure wrapper key + directional IVs + packet counters | wrap/unwrap outer packets | established | disconnect resets per-session material |

Persistence boundary: only LTK/EDIV/RAND are long-lived. STK, host/device randoms, session key, SKD values, secure-session IVs and packet counters are connection-scoped and are cleared/recreated. The reference event log never records these secret byte values.

This state model is `SUPPORTED`. Hardware acceptance of watch prompt timing, disconnect/retry behavior and reboot persistence is recorded by D-PLAN-0002/D-PLAN-0003.

### P-0213 — System-bond and Garmin-auth pairing routes (`SUPPORTED`, S-0021/S-0027; T-0037/T-0074)

Android/BlueZ bonding and Garmin GFDI authentication are separate authentication routes. `DeviceConfiguration.m55705b()` is true when LTK, EDIV, or RAND is already present.

For a fresh app pairing record with no LTK/EDIV/RAND, Garmin Connect passes `requiresBond=true`; the BLE layer performs the OS Bluetooth bond before GFDI initialization. At GFDI initialization `DefaultAuthDelegate.isGarminAuthAllowed()` returns false for a bonded remote (except the explicit Android-manufacturer `VIVO` special case). `aq2/C2861i` therefore installs `bq2/C4587y` StubAuthHandler instead of the 5101–5111 AuthHandler. StubAuth registers 5101 only to reject it, and otherwise completes authentication setup with null LTK/session values. Thus the normal system-bond route does **not** also run proprietary Garmin authentication.

The proprietary Garmin-auth route is used while the remote is not system-bonded. It runs P-0201..P-0212 and persists LTK/EDIV/RAND. Once that record exists, the connection manager registers it in `AuthRegistry`, opens the pairing connection with `requiresBond=false`, and uses 5102 for persistent reconnect.

The client implements both routes. With no GFDI record, default `pair` requests the OS bond; after handshake it accepts StubAuth/no-secure-session as the expected bonded result. With saved LTK/EDIV/RAND it skips a new bond and runs Garmin-auth reconnect. `--system-bond` forces the bonded route; `--garmin-auth` forces the unbonded 5101–5111 route. These overrides are mutually exclusive. On Linux the BlueZ bond path installs a temporary KeyboardDisplay Agent1 so terminal passkey/confirmation requests are actionable.

The bond state uses a 45-second timeout and two attempts, matching the one automatic retry in the pairing strategy. On successful proprietary long-term authentication, `AuthRegistry` persists/exposes exactly LTK (16 bytes), EDIV (2 bytes), and RAND (8 bytes). STK, session key, SKD, IVs, and packet counters remain connection-scoped.

---

## Layer 5 — Handshake/basic semantic messages

### P-0300 — Message ID census (`SUPPORTED`, S-0006/S-0008)

The currently identified watch-facing message-family names are captured in `analysis/inventory/message_ids.json` and `src/garmin_proto_lab/messages.py`. There are 57 IDs in the current census, spanning acknowledgements, file transfer, FIT, weather/live-tracking, battery/device info, notifications, configuration/time, protobuf transport and authentication/session.

The census is not considered dynamically complete until the required workflow matrix has been traced on the target watch.

### P-0301 — Device Information 5024 (`SUPPORTED`, S-0008; T-0040)

The watch sends Device Information during handshake and the host parses:

```text
uint16 LE  protocol_version
uint16 LE  product_number
uint32 LE  unit_id
uint16 LE  software_version
uint16 LE  max_packet_size
u8 + bytes bluetooth_friendly_name
u8 + bytes device_name
u8 + bytes model_name
```

On products that carry the extended section, remaining bytes begin with a one-byte dual-pairing boolean. If true, six BLE MAC bytes and six Classic-Bluetooth MAC bytes follow; each six-byte field is reversed for normal human MAC display. A further limitation byte may follow.

Products 2787, 3192 and 3307 are statically exempted from this extended parse path.

The received max packet size feeds the GFDI dispatcher limit.

### P-0302 — Host handshake response to 5024 (`SUPPORTED`, S-0008; T-0043)

The host's 5024 acknowledgement data constructs a host identity record with a selected protocol version (legacy 113 vs newer 150 based on peer protocol), sentinel product/unit/max-packet fields, app version, and length-prefixed Android device identity strings. The final byte is set to 1 in this build.

Compatibility code should initially minimize identity fields and reproduce only fields proven necessary by a target watch, rather than impersonating a Garmin app/device string unnecessarily.

### P-0303 — Configuration 5050 and host fitness capability set (`SUPPORTED`, S-0008/S-0028; T-0043/T-0079)

Configuration is represented as a length-prefixed bitset/capability vector. The watch and host exchange/acknowledge these capability bits during handshake. Unknown peer bits are preserved.

Garmin Connect constructs its host Configuration from `client_config.xml` capability ordinals plus each active handler's configuration set. The direct pairing/fitness client advertises only recovered capabilities used by implemented workflows: Connect Mobile FIT Link 0, Sync 3, Device Initiates Sync 4, Host Initiated Sync Requests 5, Request Pair Flow 64 and Current Time Request Support 71. Fitness sync additionally advertises Explicit Archive 29 and bit 90, which is `SYNC_2` in `SupportedCapability` and is also contributed by `FileTransferManager` to gate next-generation FileAccess. Notification workflows add GNCS 6. Peer bit 95 gates the Smart/Core FeatureCapabilities request; it is not a host application capability from `SupportedCapability`.

### P-0304 — Queued Download 5027 (`SUPPORTED`, S-0008/S-0015; T-0044)

Queued Download begins with a one-byte bitset length followed by exactly that many category-bitset bytes. Bit `n` denotes download category `n`; unknown bits are preserved. The recovered handler acknowledges a valid queued-download request with response data byte zero.

### P-0310 — Battery Status 5023 (`SUPPORTED`, S-0008; T-0041)

Minimum observed payload is six bytes. Watch battery state uses `payload[0] & 0x70`:

| Bits | State |
|---:|---|
| `0x10` | GOOD |
| `0x20` | OK |
| `0x30` | LOW |
| `0x40` | CRITICAL |
| `0x50` | NEW |
| `0x70` | INVALID |

Capacity percentage is `payload[2]`.

The legacy host phone-battery update is six bytes: `FF 00 <percent> FF FF FF`.

### P-0320 — Current Time 5052 (`SUPPORTED`, S-0008; T-0042)

A request shorter than four bytes is rejected with length error. A normal response has 20 bytes of response-specific data:

```text
0..3    first four request bytes echoed unchanged
4..7    current time, uint32 LE, seconds since Garmin epoch
8..11   local UTC offset in seconds, signed value serialized as uint32 LE
12..15  next DST start, Garmin epoch seconds (or zero when unavailable)
16..19  next DST end, Garmin epoch seconds (or zero when unavailable)
```

Garmin epoch used by this build is Unix epoch plus `631065600` seconds, i.e. 1989-12-31 00:00:00 UTC.

The independent implementation performs a bounded timezone transition search and returns the next DST start/end pair when the local zone provides one; fixed/no-transition zones return zeroes. Tests cover the Garmin epoch, UTC response and a known North American 2026 transition pair.

### P-0330 — Sync Request 5037 (`SUPPORTED`, S-0015; T-0045)

Legacy Sync Request payload is:

```text
byte0    option: 0 manual, 1 invisible, 2 visible-as-needed
byte1    N, number of category-bitset bytes
2..N+1   category bitset, least-significant bit first within each byte
```

The receiver validates that all declared bitset bytes are present and ACKs a valid request. Unknown option values must be preserved/reported rather than silently interpreted by the independent client.

### P-0331 — Download-category bit positions (`SUPPORTED`, S-0015; T-0045)

The static higher-level sync queue names the legacy bit positions. The full independent enum is in `src/garmin_proto_lab/sync.py`; examples required for target workflow analysis include activities=5, software update=8, device settings=9, activity summary=21, sleep=26, HRV status=36 and uninstall app=39.

A category bit indicates a class of downloadable work, not a concrete file index. It must not be used to invent a target-watch file number.

### P-0332 — File Ready 5009 (`SUPPORTED`, S-0015; T-0046)

A File Ready announcement has at least 16 bytes:

```text
0..1    file index, uint16 LE
2       data type
3..5    three-byte identifier
6       RESERVED byte, preserved
7       file flags
8..11   file size, uint32 LE
12..15  timestamp/value, uint32 LE
```

The first identifier byte is consumed by the Android sync bridge as a subtype. File flag masks are READ `0x80`, WRITE `0x40`, ERASE `0x20`, ARCHIVE `0x10`, APPEND `0x08`, CRYPTO `0x04`. Concrete activity/health file indices, types and subtype meanings remain target-dependent and require D-PLAN-0005.

---

## Layer 6 — File transfer

### P-0400 — File-transfer message family (`SUPPORTED`, S-0009)

Core IDs:

- 5002 Download File (watch -> host initiation)
- 5003 Upload File (host -> watch initiation)
- 5004 File Data
- 5005 Create File
- 5006 Delete File
- 5007 Directory Filter
- 5008 Set File Flags
- 5009 File Ready
- 5022 Cancel File Transfer
- 5031 Supported File Types
- 5054 Compressed File Data

### P-0401 — Download File 5002 request (`SUPPORTED`, S-0009)

The recovered host request is 14 bytes:

```text
uint16 LE file_index
uint32 LE data_offset = 0 in the observed initial request
uint8     request/version flag = 1 in the observed path
uint16 LE reserved = 0 in the recovered read task
uint32 LE reserved = 0 in the recovered read task
uint8     compression_requested (0/1)
```

The recovered read task writes literal zero to both reserved fields on every Download File request and never branches on them. The implementation therefore fixes them to zero by default while preserving explicit codec fields for wire tests.

Response status byte:

| Value | Meaning |
|---:|---|
| 0 | success; bytes 1..4 are uint32 LE file size |
| 1 | file index does not exist |
| 2 | file index not readable |
| 3 | not ready; app retries after ~1 s while retry budget remains |
| 4 | invalid request |
| 5 | CRC mismatch |
| 6 | requested range exceeds file size |

### P-0402 — Ordinary File Data 5004 (`SUPPORTED`, S-0009/S-0016; T-0047)

Payload received from the watch:

```text
byte 0      transfer marker/flag (role not fully named)
bytes 1..2  running CRC16 LE
bytes 3..6  data offset uint32 LE
bytes 7..   file data
```

The receiver requires the data offset to equal the current output offset, tolerates/re-acks a duplicate prior offset, bounds repeated invalid packets, updates the running CRC over file bytes, writes data and responds with:

```text
uint8     status
uint32 LE current output offset
```

Known response statuses include 0 success, 2 abort/cancel, 3 CRC mismatch and 4 offset mismatch. The exact CRC checkpoint ordering will be kept `SUPPORTED` until verified against smali + a fixture/capture.

### P-0403 — Host ordinary File Data generation (`SUPPORTED`, S-0009)

For host-to-watch transfer, max data bytes per GFDI message are `dispatcher_max_payload - 7`. The seven-byte prefix mirrors P-0402: first marker/flag, running CRC16 at 1..2, current data offset at 3..6. The first-chunk boolean is represented in byte 0 in the recovered sender.

The watch response contains status plus next expected offset; unexpected offsets abort rather than silently skipping data.

### P-0404 — Upload File 5003 initiation/resume (`SUPPORTED`, S-0009)

The upload request includes file index, total file size, zero-valued initial resume/checkpoint fields and optional compression window metadata. Successful response supplies resume offset, available/maximum size and running CRC, with optional negotiated compression window bits. The sender recomputes CRC to the proposed resume offset; if it does not match, it restarts at zero.

This gives a statically defined resume/integrity model, but exact optional request fields and target-device policy require a dynamic transfer before they are labelled confirmed.

### P-0405 — Compressed File Data 5054 (`SUPPORTED`, S-0009/S-0016; T-0048)

Compressed chunk payload:

```text
byte 0      packet counter modulo 256
byte 1      flags; bit 1 (0x02) marks the end of the current compressed block/stream
bytes 2..5  cumulative decompressed output position uint32 LE
bytes 6..7  running CRC16 LE of decompressed output
bytes 8..   DEFLATE-compressed bytes
```

The receiver increments the packet counter modulo 256, inflates bytes, tracks cumulative output CRC and validates the supplied checkpoint when output reaches the indicated position.

Response begins with echoed packet counter and status:

| Status | Meaning |
|---:|---|
| 0 | success |
| 1 | abort/internal malformed transfer |
| 2 | incorrect CRC |
| 3 | decompression failure |
| 4 | packet-counter mismatch; third byte carries expected counter |

Negotiated compression `windowBits` is accepted only in range 9..15 in the recovered sender.

### P-0406 — Supported File Types 5031 (`SUPPORTED`, S-0009/S-0016; T-0049)

Request has empty payload. Response begins with an entry-count byte followed by exactly that many:

```text
dataType:u8 | subType:u8 | nameLength:u8 | nameBytes
```

If the device explicitly returns GFDI UNKNOWN_OR_NOT_SUPPORTED, the static compatibility layer falls back to `(128,4,"FIT_TYPE_4")` and `(128,36,"FIT_TYPE_36")`. This fallback is implemented, but it is not used to invent which type the owner's watch stores.

### P-0407 — Directory filters and index-zero directory (`SUPPORTED`, S-0016; T-0049/T-0055)

Message 5007 applies one byte: 0 no filter, 1 default, 3 pending-uploads-only. Response data byte zero means supported; nonzero means unsupported. Directory enumeration then reads file index zero, normally requesting compression.

The downloaded directory starts with an opaque 16-byte header that this app path never interprets. Every subsequent complete 16-byte record is:

```text
0..1    file index, uint16 LE
2       data type
3..5    three-byte identifier
6       RESERVED_6, preserved
7       file flags
8..11   size, uint32 LE
12..15  timestamp/value, uint32 LE
```

The first identifier byte is exposed as subtype. Known file-flag bits are READ `0x80`, WRITE `0x40`, ERASE `0x20`, ARCHIVE `0x10`, APPEND `0x08`, CRYPTO `0x04`. Higher-level Garmin sync code only exposes directory records carrying READ and not ARCHIVE.

Directory header bytes are preserved raw. RESERVED_6 is preserved and the recovered path never branches on it.

### P-0408 — Archive and special file indexes (`SUPPORTED`, S-0016; T-0055)

Message 5008 applies file operations. Archive is `index:u16LE | 0x10`. File index zero is the directory; static framework code also exposes Garmin device XML at index `0xFFFD`.

### P-0409 — Read orchestration, cancellation and compression fallback (`SUPPORTED`, S-0016; T-0056)

A read creates an active receiver before issuing 5002 so immediately arriving 5004/5054 data can be consumed. Download status NOT_READY is retried once after about one second. A requested compressed read that is cancelled by the remote device is retried uncompressed in the recovered file manager.

Message 5022 cancels an active transfer and is ACKed. If 5004 arrives without an active receiver, the host ACK data is a one-byte abort status `2`; if 5054 arrives without a receiver, it responds with `counter,1`. The independent client mirrors these fail-closed responses.

### P-0410 — Activity/health acquisition path (`SUPPORTED`, S-0015/S-0016; T-0056)

The protocol does not require a hard-coded activity file index. The generic acquisition sequence is:

1. receive 5037/5027/5009 sync intent or proactively enumerate,
2. apply directory filter (often pending uploads),
3. query 5031 supported file type labels,
4. read directory index 0,
5. select readable, non-archived directory entries according to application policy/subtype,
6. read the selected dynamic file index through 5002 + 5004/5054,
7. validate transfer offset/CRC/decompression,
8. archive the successfully processed remote file with 5008 when policy calls for it.

This establishes the end-to-end generic file transport and removes the need to guess file indexes. Which concrete directory records on the owner's watch correspond to activities, health/monitoring, sleep, HRV, etc. remains device data and must be established with D-PLAN-0005 before those semantics are labelled confirmed.

### P-0411 — FIT subtype/content classification (`SUPPORTED`, S-0017; T-0057)

Generated FIT-profile code in the package maps file type/subtype values including ACTIVITY=4, WEIGHT=9, BLOOD_PRESSURE=14, MONITORING_A=15, MONITORING_B=32 and SLEEP_DATA=49. For directory entries whose data type is the observed FIT family value 128, these subtype names can be used as static classification hints.

The independent runtime does not rely solely on the directory hint. Its minimal FIT inspector validates the `.FIT` container header and walks definition/data records far enough to locate File ID (global message 0), field 0, whose enum value is the file type. A downloaded activity can therefore be cross-checked as subtype 4 in both the directory and the file content before exposing it semantically. Unknown FIT types remain numeric.

The runtime also validates the trailing FIT file CRC before publishing a downloaded object.

### P-0412 — Generic FIT records and activity/wellness projections (`SUPPORTED`, S-0017/S-0024/S-0029; T-0077/T-0080/T-0082)

After the FIT container passes header and file CRC validation, definition records establish local-message layouts. The decoder preserves numeric global message and field identifiers, decodes standard FIT base types with the architecture declared by each definition, preserves developer-field bytes, and reconstructs compressed-timestamp headers from the previous timestamp plus the five-bit offset. No Garmin-private profile class is required to retain fields the application does not yet name.

For standard FIT Record messages (global message 20), the semantic activity projection exposes timestamp, position latitude/longitude, altitude, heart rate, cadence, distance, speed/enhanced speed, power and temperature using the standard FIT scales. The public Garmin FIT Profile 21.214 additionally resolves semantic wellness projections for WEIGHT_SCALE (30), BLOOD_PRESSURE (51), MONITORING (55), HRV (78), MONITORING_INFO (103), HR (132), MONITORING_HR_DATA (211), STRESS_LEVEL (227), SPO2_DATA (269), SLEEP_LEVEL (275), RESPIRATION_RATE (297), and HSA_BODY_BATTERY_DATA (314), including the standard field scales and sleep/SpO2/activity enums. Timestamps use the Garmin/FIT epoch already used by the GFDI time layer. FIT base-type invalid sentinels map to null instead of fabricated measurements. The JSON projection retains every numeric record/field identifier, raw field bytes and developer bytes alongside these semantic values, so profile extensions are not discarded. Standard Session (18), Lap (19), and Activity (34) records are additionally projected into activity aggregates covering timers, distance, calories, speed, heart rate, cadence, power, ascent/descent, training effect, lap count, sport and sub-sport while retaining their raw source fields.

---

## Layer 7 — Smart notifications / GNCS

### P-0500 — GNCS message IDs (`SUPPORTED`, S-0010)

- 5033 Notification Source
- 5034 Control Point
- 5035 Data Source
- 5036 Notification Service Subscription

### P-0501 — Subscription 5036 (`SUPPORTED`, S-0010)

Request is two bytes:

```text
byte0 intent: 0 unsubscribe, 1 subscribe
byte1 feature flags
```

A known feature bit (bit 0) relates to phone-number/raw-SMS-reply capability in the static handler.

Response-specific data is three bytes:

```text
byte0 status: 0 success, 1 not supported, 2 not ready
byte1 echoed/negotiated intent
byte2 negotiated feature flags
```

### P-0502 — GNCS Data Source 5035 (`SUPPORTED`, S-0010)

Chunk layout:

```text
uint16 LE total notification/ANCS payload size
uint16 LE running CRC
uint16 LE data offset
bytes     chunk data
```

Static maximum aggregate payload is 8192 bytes. Known receiver responses include success, resend-last, abort, CRC mismatch and offset mismatch. Some paths can transform the chunk with the same XXTEA primitive using a padding mode distinct from secure-session wrapping.

The transport layer alone is not the notification semantic grammar; P-0503..P-0506 define the recovered ANCS-shaped payloads. Target-watch capability and user-visible behavior still require device verification.

### P-0503 — Notification Source 5033 (`SUPPORTED`, S-0014; T-0050)

The unencrypted semantic payload is exactly nine bytes:

```text
offset size field
0      1    event: 0 added, 1 modified, 2 removed
1      1    event flags
2      1    category
3      1    category count (host caps values above 127 to 127)
4      4    notification ID, uint32 little-endian
8      1    feature flags
```

Event flag bits are silent `0x01`, important `0x02`, pre-existing `0x04`, positive-action `0x08`, negative-action `0x10`. Categories are other=0, incoming call=1, missed call=2, voicemail=3, social=4, schedule=5, email=6, news=7, health/fitness=8, business/finance=9, location=10, entertainment=11, SMS=12. Feature bits are phone-number available `0x01`, Android actions `0x02`, media `0x04`.

### P-0504 — Control Point 5034 attribute commands (`SUPPORTED`, S-0014; T-0051/T-0052)

Control-point command IDs are:

- `0x00` get notification attributes,
- `0x01` get application attributes,
- `0x02` perform the standard notification action,
- `0x80` perform an Android-specific action.

Get-notification-attributes begins with command byte, uint32 LE notification ID, then requested attribute descriptors. Attribute IDs 1/2/3/126/129 (title/subtitle/message/phone/conversation) carry a uint16 LE maximum length. Attribute 127 (actions) carries one-byte maximum value length, one-byte maximum action count and one-byte action flags. Other selectors carry no request-size suffix.

The corresponding data response begins with command and notification ID, then repeated `attribute_id:u8 | value_length:u16LE | value`. App-attribute command 1 uses a NUL-terminated application identifier followed by attribute IDs; its response uses the same `id + uint16 length + value` records. The statically observed app attribute is display name ID 0.

Before validating 5034, the Garmin path removes padded XXTEA protection when a GNCS session key is present. The outer GFDI acknowledgement then carries two semantic bytes: response type (`0` success, `1` ANCS error; an enum value `2` exists for invalid parameters) and an ANCS error code. Recovered ANCS errors are 0 no-error, 160 unknown command, 161 invalid/malformed command, 162 invalid parameter. A valid command is forwarded to the notification provider only after this acknowledgement.

### P-0505 — Notification actions/dismissal (`SUPPORTED`, S-0014; T-0053)

A standard action request is six bytes: command `0x02`, uint32 LE notification ID, then action ID (positive=0, negative=1). Android-specific action command `0x80` uses the same notification ID, an application-defined action ID byte, and optional NUL-terminated UTF-8 input text.

The Android-actions attribute value is a count byte followed by descriptors. Each descriptor is `action_id:u8 | flags:u8 | title_length:u8 | title_bytes`. Known flags are request-input `0x01`, positive `0x02`, negative `0x04`, dismiss `0x08`. These flags provide the protocol representation needed for action/dismissal behavior, but which actions a particular watch requests or displays remains dynamic.

### P-0506 — Notification-source encryption (`SUPPORTED`, S-0014; T-0054)

When the GNCS notification sender has no session key, P-0503 bytes are sent directly as message 5033 request data. When a GNCS session key is present, the semantic payload is transformed with the self-describing padded XXTEA mode before being sent as 5033. This is the same padded transform used for GNCS 5035 chunk data and is distinct from the secure-session outer packet wrapper.

### P-0507 — Notification attribute/action lifecycle (`SUPPORTED`, S-0014; T-0058)

After a valid 5034 Control Point acknowledgement, the host serves requested notification attributes through 5035 Data Source. The recovered attribute values are application identifier, date text `yyyyMMdd'T'HHmmss`, message, decimal Java-string message length, subtitle, title, positive/negative action labels, phone number, conversation ID, serialized Android actions, and decimal media-object count. Requested string lengths are honored on UTF-8 boundaries; action count/title length/input-support flags bound the actions response.

If the requested notification is no longer active, the host sends a 5033 removed source for that notification ID instead of inventing attributes. App-attribute request ID 0 returns the application display name. Perform-notification-action and Android-action requests are surfaced to application callbacks; the transport layer does not execute arbitrary application actions itself.

The reference client therefore owns an explicit `NotificationRecord` cache, automatically serves only records supplied by its application, and emits action requests semantically for application policy.

---

## Layer 8 — GDI Smart protobuf transport

### P-0600 — Protobuf chunk transport 5043/5044/5045 (`SUPPORTED`, S-0018; T-0061)

Messages 5043 (request) and 5044 (response) carry serialized protobuf bytes in independently acknowledged chunks:

```text
0..1    request ID, uint16 LE
2..5    data offset, uint32 LE
6..9    total serialized length, uint32 LE
10..13  chunk length, uint32 LE
14..    chunk bytes
```

Maximum chunk data is `current GFDI payload limit - 14`. The eight-byte GFDI acknowledgement payload is `requestId:u16LE | offset:u32LE | failed:u8 | status:u8`. Status values are no-error 0, unknown request 100, duplicate packet 101, missing packet 102, exceeded protobuf length 103, parse error 200, and unknown protobuf message 201. Duplicate is transport-success; missing/length/parse/unknown are failures.

Message 5045 carries the uint16 request ID. It cancels pending response state and is acknowledged by echoing the request ID. A failed or cancelled host request attempts 5045 cancellation; cancellation errors do not replace the originating request error.

### P-0601 — Smart extension envelope (`SUPPORTED`, S-0018; T-0062)

`GDI.Proto.Smart.Smart` contains no ordinary fields. Its payload consists of protobuf extension fields. For the v1 workflows:

- Smart field 13: Core service.
- Smart field 49: GNCS service traffic.

The compatibility runtime parses standard protobuf wire types with finite field and length bounds and preserves unknown fields numerically. It does not load Garmin generated protobuf classes.

### P-0602 — Core feature-capability exchange (`SUPPORTED`, S-0018; T-0062/T-0063)

Within Core service, field 8 is Feature Capabilities Request, field 9 is Feature Capabilities Response, and field 14 is Connection Ready Notification. The static GFDI startup path performs the capability request only when the peer legacy Configuration contains flag 95.

Feature Capabilities Request has optional `garmin_guid:bytes=1`, `client_version:uint32=2`, and `display_name:string=3`; the independent client leaves these unset unless a future target proves they are required. Response fields are `guid_status=1` (UNSET=0, MATCH=1, NO_MATCH=2) and `version:uint32=2`.

### P-0603 — GNCS protobuf capabilities (`SUPPORTED`, S-0018; T-0062/T-0063)

GNCS is a message-scoped extension field 12 inside Core Feature Capabilities. Request fields are:

```text
1  np_version:uint32
2  notification_disabled_reason:enum (optional)
3  default_messaging_app_id:string
4  default_dialer_app_id:string
```

Disabled reasons are 1 low-RAM/mobile-disabled, 2 permission-not-granted, 3 service-not-bound. Semantic version `A.B.C` becomes `(A<<16)|(B<<8)|C`. The reference client advertises its own version and does not reuse Garmin's build version.

Response fields are `nc_version:uint32=1` and `support_blocked_apps:bool=2`. Static Garmin behavior treats `nc_version >= 1` as support for modified-after-added notification semantics and exposes the blocked-app capability when field 2 is true.

### P-0604 — Connection-ready notification (`SUPPORTED`, S-0018; T-0063)

Core field 14 is an empty Connection Ready Notification carried in an incoming Smart protobuf request. It is treated as a handled notification, not as a reason to fabricate a semantic protobuf response. Feature handlers are notified after it arrives. Exact timing relative to authentication and notification subscription remains a target-watch observation.

---

## Layer 9 — Next-generation FileAccess over MultiLink

### P-0700 — FileAccess protobuf service (`SUPPORTED`, S-0019/S-0023/S-0030; T-0064/T-0065/T-0070/T-0081)

Watch Configuration bit 90 is the runtime selector for FileAccess/Sync2. Bit 95 only gates the optional Core FeatureCapabilities exchange; a missing FileAccess capability extension does not disable FileAccess itself.

Legacy Configuration flag 90 enables the FileAccess manager. Smart extension field 43 contains one FileAccess `Service` message. Core Feature Capabilities extension field 16 carries FileAccess capabilities.

Recovered service fields:

| Field | Message | Field | Message |
|---:|---|---:|---|
| 1 | Pull Item Request | 2 | Pull Item Response |
| 3 | Push Item Request | 4 | Push Item Response |
| 5 | Transfer Status Request | 21 | Transfer Status Response |
| 6 | Priority Update Request | 7 | Priority Update Response |
| 9 | Item List Request | 10 | Item List Response |
| 11 | Item List Cancel Notification | 12 | Item Added Notification |
| 13 | Delete Item Request | 14 | Delete Item Response |
| 15 | Modify Flags Request | 16 | Modify Flags Response |
| 17 | Item Updated Notification | 18 | Cancel Transfer Request |
| 19 | Cancel Transfer Response | 20 | Resource Update Notification |
| 22 | Sync Button Notification | 23 | Software Update Part Number Request |
| 24 | Software Update Part Number Response | 25 | Get Item Checksum Request |
| 26 | Get Item Checksum Response |  |  |

Item List request/response fields support transaction/session IDs, maximum count, exclude/get flag UUIDs, included data types and modified-time metadata. A response `session_id` pages the same listing; the next request contains only that session ID. A terminal page omits `session_id` and can return `next_transaction_id`. GDXML data-type strings can be sent once with a string key and referenced by key on later pages; the client maintains that table across the transaction.

Pull response selects transport enum 0 (`MULTILINK_TRANSPORT_PIPE`), returns a transfer handle and can return a compression window. Push uses the same transport selection plus a device-selected offset. Transfer Status request/response are fields 5/21 and close the negotiated transfer lifecycle. Cancel Transfer fields 18/19 return SUCCESS=0 or UNKNOWN_TRANSFER=1; both are idempotent cleanup success.

Delete, priority, flag-modification and notification enums are modeled explicitly in `file_access_proto.py`. Unknown numeric enum values are preserved numerically.

Recovered fitness-facing names include `FIT_TYPE_4` activity, `FIT_TYPE_32` monitoring and `FIT_TYPE_49` sleep; additional modeled FIT health types include weight, blood pressure and monitoring A.

### P-0701 — MultiLink GATT registration and info pages (`SUPPORTED`, S-0020/S-0026; T-0066/T-0078)

MultiLink service UUID is `6A4E2800-667B-11E3-949A-0800200C9A66`. Candidate data characteristics are `6A4E2810..2819`; paired write characteristics are `6A4E2820..2829` when present. Control packets begin with zero.

```text
register request:  00 00 | client_id:u64LE | service_id:u16LE | flags:u8
register response: 00 01 | client_id:u64LE | service_id:u16LE | status:u8 | ...
close handle:      00 02 | client_id:u64LE | service_id:u16LE | handle:u8
close all:         00 05 | client_id:u64LE | 0000
```

Register flag `0x02` requests a reliable service. Status 0 returns handle, optional flags (`bit0=reliable`) and optional revision. Statuses 1/2/3/4 are invalid-service, pending-auth, already-in-use and rejected. Status 3 can carry an alternate characteristic. Registration service ID is 4. FileAccess pipe service IDs are `0x2018,0x4018,0x6018,0x8018,0xA018,0xC018,0xE018`.

The MultiLink `client_id` is the GFDI client UUID serialized as uint64 LE. Garmin Connect's bundled `client_config.xml` sets it to `0x01`, and the MultiLink subscriber passes that value directly into the registration communicator (S-0026). The compatibility client therefore defaults to `0x01`; `--multilink-client-id` can override it for diagnostic comparisons.

### P-0702 — MLR reliable packet header (`SUPPORTED`, S-0020; T-0067/T-0071)

Non-reliable MultiLink data is `handle:u8 | payload`, with handle below `0x80`. Reliable handles are `0x80..0x87` and use two bytes carrying six-bit sequence number `SN` and cumulative ACK/request number `RN`:

```text
b0 = 0x80 | ((handle & 7) << 4) | (RN >> 2)
b1 = ((RN & 3) << 6) | SN
payload follows
```

Normal connections use 64 sequence values. Native mode `0xFF` returns a sequence count of 8 for formatter diagnostics; other nonzero modes return `0xFF`. Payload capacity is `max_write_length - 2` and packet count is `(data_length + max_write_length - 3) // (max_write_length - 2)`.

Native formatter self-test vectors used by the implementation tests are `05 01`, `d0 c2 ff`, `92 cd ff de a2`, and `ff ff 01 02 03`.

### P-0703 — MLR ARQ, ACK and retransmission state (`SUPPORTED`, S-0020; T-0071/T-0072)

The normal reliable connection initializes:

```text
sequence modulus = 64
send window      = 32 packets
window maximum   = 63 packets
initial RTO      = 1000 ms
minimum RTO      = 500 ms
maximum backoff  = 20000 ms
```

RN is a cumulative ACK of the next expected peer sequence. An advancing ACK removes all fully acknowledged packets and increments the send window by one through 63. The receiver increments a pending-ACK counter for received data and sends a standalone cumulative ACK after five data packets. The first unacknowledged receive starts a 10 ms deferred-ACK timer; expiry sends the ACK sooner. Any outbound reliable packet carries the current RN and therefore clears the pending standalone ACK.

RTT estimation uses:

```text
alpha = 1/8
beta  = 1/4
RTTVAR <- beta*abs(previous_sRTT - sample) + (1-beta)*RTTVAR
sRTT   <- alpha*sample + (1-alpha)*previous_sRTT
RTO    = max(500 ms, sRTT + max(0.5*sRTT, 4*RTTVAR))
```

When multiple RTT samples arrive within one sample interval, alpha/beta are scaled by elapsed/sample, matching the native code. Retransmission timeout halves the send window through a floor of one, doubles RTO through 20 seconds, resets the send sequence to the unacknowledged base and retransmits from that base.

A packet whose SN is not the current receive-next value is not delivered to the service. RN remains at the missing sequence so the sender retransmits the gap.

### P-0704 — FileAccess transport-pipe pull, compression and resume (`SUPPORTED`, S-0019/S-0020/S-0024; T-0068/T-0073)

After Pull returns a transfer handle, the host opens a reliable FileAccess MultiLink service and sends:

```text
00 | direction:u8 | transfer_handle:u64LE
```

Pipe direction is 0 read / 1 write. Configure response contains command/echo, general status and configure status. General status is 0 success, 1 unknown command, 2 invalid command data. When general status is success, configure status is 0 success, 1 unknown transfer handle, 2 unexpected transfer direction, 3 unexpected transfer size.

Accepted MLR payload bytes are appended in sequence order until the listed clear item size is reached. A compressed Pull negotiates a zlib window and the receiver inflates the MLR payload stream; the FileAccess item size remains the clear/decompressed size. The transfer does not complete until the device's Transfer Status request is answered.

Pull request field `offset` is a clear-file byte offset. Resume sends the saved clear prefix length as that offset, receives the remaining suffix and concatenates the exact prefix + suffix. Compressed interrupted transfers resume as an uncompressed byte-offset pull so resume state is independent of zlib stream history.

On a local transport/configuration/data failure the client issues Cancel Transfer. A pending device Transfer Status request is answered before the failure is surfaced. Cancellation failure does not replace the originating transfer error.

### P-0705 — FileAccess truncated-MD5 (`SUPPORTED`, S-0023; T-0070/T-0073)

Feature Capabilities reports checksum method 1 as `TRUNCATED_MD5` and can report a maximum file size. Get Item Checksum request field 25 carries the item UUID. Response field 26 contains item UUID, result enum and checksum fixed64.

For a clear file `F`:

```text
digest = MD5(F)
checksum_bytes = digest[0:8]
wire_uint64 = little_endian_uint64(checksum_bytes)
```

Result values are 0 unspecified, 1 success, 2 item missing, 3 size too large, 4 internal error, 5 device busy. The synchronizer requests the checksum only when the advertised method/size bound permits it and rejects a mismatch.

### P-0706 — Fitness extraction persistence and integrity (`SUPPORTED`, S-0017/S-0024; T-0057/T-0073/T-0074)

A fitness file is published only after:

1. FileAccess/legacy transport completes;
2. server truncated-MD5 matches when FileAccess advertises it;
3. FIT signature/header/data length is valid;
4. FIT header CRC is valid when present;
5. File ID global message 0 resolves to an activity/health type;
6. trailing full-file FIT CRC matches.

`fitness-sync` writes mode-`0600` clear bytes to a `.part` file while FileAccess data arrives. The next run resumes from the saved byte length. A complete valid file is atomically renamed to `.fit` and reused on subsequent syncs. The output directory is mode `0700`.

When FileAccess is not advertised, `fitness-sync` uses the legacy directory/5002/5004/5054 path and applies the same FIT validation before publishing the file.


### Second-client implementation sequence

A second implementation can reproduce the pairing-to-fitness workflow from this specification without decompiler output by composing the layers in this order:

1. **Discover and connect (P-0001..P-0006/P-0213).** Scan BLE and select the watch. With no proprietary GFDI record, request the OS Bluetooth bond; with saved LTK/EDIV/RAND use the unbonded Garmin-auth reconnect route. Discover services, request MTU 515, and use a direct GFDI pair or the MultiLink service-1 fallback from P-0014. `--system-bond` and `--garmin-auth` force the two pairing routes for testing.
2. **Create the byte stream (P-0100..P-0115).** Treat notification callbacks as arbitrary stream chunks, split at zero delimiters, COBS-decode, validate GFDI lengths/CRC, allocate 5-bit transaction IDs and correlate acknowledgements by transaction + original message type.
3. **Complete startup handshake (P-0301..P-0304).** Answer device-information/configuration requests, retain peer configuration flags and set the protobuf payload limit from device information before using Smart protobuf.
4. **Authenticate (P-0201..P-0213).** On a system-bonded route, complete startup without proprietary Garmin session keys. On an unbonded route, handle 5101 negotiation; with saved LTK/EDIV/RAND attempt 5102 reconnect, otherwise process 5103/5104/5105/5106/5107/5108/5109/5111 fresh auth. Persist only LTK/EDIV/RAND and install the secure wrapper only after the plaintext 5111 acknowledgement.
5. **Enable semantic services.** Battery/time/status use P-0310/P-0320/P-0330. GNCS notifications use P-0500..P-0507 when host/peer capabilities enable them.
6. **Probe FileAccess (P-0600..P-0604/P-0700).** If peer Configuration includes bit 90, use FileAccess/Sync2. If peer bit 95 is also present, send the Core feature-capabilities request and record optional FileAccess checksum/custom-flag metadata when returned. If peer bit 90 is absent, use the legacy path in P-0400..P-0411.
7. **List fitness objects (P-0700).** Send Item List and follow `session_id` pages. Preserve the GDXML string-key table across pages. Select activity/health types such as `FIT_TYPE_4`, `FIT_TYPE_32`, `FIT_TYPE_49` and the other modeled health FIT types; item UUIDs are runtime identifiers, not constants.
8. **Register MultiLink (P-0701).** Subscribe to a `281x` data characteristic, register service ID 4, then register one reliable FileAccess pipe service. Respect an alternate characteristic returned with status 3. Use the resulting reliable handle `0x80..0x87`.
9. **Run MLR (P-0702/P-0703).** Encode/decode SN/RN headers, begin with a 32-packet window, advance cumulative ACKs, emit ACK on five packets or after 10 ms, update RTT/RTO, and on timeout halve the window and retransmit from the unacknowledged base.
10. **Pull/resume the item (P-0704).** Send FileAccess Pull with `offset`, configure the pipe `00 | READ | transfer_handle:u64LE`, collect clear bytes in sequence, optionally inflate negotiated zlib data, and answer the device Transfer Status request. For interrupted transfer, persist clear bytes and issue the next Pull with offset equal to the prefix length.
11. **Verify integrity (P-0705/P-0706).** When advertised, query truncated-MD5 and compare it over the complete clear file. Validate FIT signature, header CRC, File ID and trailing FIT file CRC before publishing the file.
12. **Persist/reuse.** Store pairing material in an OS-appropriate credential store. Store partial fitness bytes durably with private permissions, atomically publish complete `.fit` files, and skip a completed file after revalidating its FIT container.

The repository command implementing this sequence is `garmin-proto fitness-sync <address> --output <dir>`.

---

## Message-family census

Current names recovered from the app's own message-name mapping:

| ID | Name | ID | Name |
|---:|---|---:|---|
| 5000 | Acknowledgement | 5002 | Download File |
| 5003 | Upload File | 5004 | File Data |
| 5005 | Create File | 5006 | Delete File |
| 5007 | Directory Filter | 5008 | Set File Flags |
| 5009 | File Ready | 5011 | FIT Definition |
| 5012 | FIT Data | 5014 | Weather Request |
| 5015 | Weather Alert | 5016 | LT Tracking Request |
| 5019 | Ephemeris Data Request | 5020 | Ephemeris Data |
| 5022 | Cancel File Transfer | 5023 | Battery Status |
| 5024 | Device Information | 5025 | LT Stop Tracking |
| 5026 | Set Device Settings | 5027 | Queued Download |
| 5028 | Ephemeris EPO Request | 5029 | Ephemeris EPO Data |
| 5030 | System Event | 5031 | Supported File Types |
| 5033 | GNCS Notification Source | 5034 | GNCS Control Point |
| 5035 | GNCS Data Source | 5036 | GNCS Subscription |
| 5037 | Sync Request | 5039 | Find My Phone |
| 5040 | Cancel Find My Phone | 5041 | Music Control |
| 5042 | Music Control Capabilities | 5043 | Protobuf Request |
| 5044 | Protobuf Response | 5045 | Cancel Protobuf |
| 5046 | LT Auto Start | 5047 | LT Auto Start Cancel |
| 5048 | LT Auto Start Failure | 5049 | Music Entity Update |
| 5050 | Configuration | 5052 | Current Time |
| 5054 | Compressed File Data | 5101 | Auth Negotiation Begin |
| 5102 | LTK Reconnect | 5103 | STK Begin Generation |
| 5104 | Confirm Number | 5105 | STK Random Number |
| 5106 | STK Generation Status | 5107 | LTK Key Distribution |
| 5108 | Session SKD Distribution | 5109 | Session Key Verification |
| 5110 | Passkey Redisplay | 5111 | Secure Session |
| 5112 | Out-of-Band Passkey Data |  |  |

`src/garmin_proto_lab/messages.py` assigns every recovered ID an explicit disposition: implemented by the pairing/notification/status/fitness stack, outside the direct-fitness workflow, or `static_no_callsite`. The only named authentication ID with no sender/receiver call site in this build is 5110 Passkey Redisplay (S-0022). `tests/test_messages.py` enforces full census coverage (T-0076). A target-watch capture can add newly observed IDs to this census, but no recovered ID is left unclassified.

---

## Required dynamic experiments once the watch is available

These are deliberately written before seeing the trace to reduce confirmation bias.

### D-PLAN-0001 — Clean service discovery

Reset only the test connection state required for a clean run. Record services, characteristics, properties, descriptors, MTU callback, notification subscriptions and exact order. Resolve whether the watch uses dedicated Connect Mobile UUIDs or generic GFDI UUIDs.

### D-PLAN-0002 — Fresh pairing/authentication

From a deliberately clean pairing/application state, capture BLE/GFDI traffic while recording user-visible watch prompts. Validate 5101/5103/5104/5105/5106/5107/5108/5109/5111 ordering and all lengths. Never commit raw LTK/STK/session material; retain secrets only locally long enough to verify transforms.

### D-PLAN-0003 — Persistent reconnect

Pair once, stop the compatibility client, then separately test client restart, Bluetooth toggle, watch reboot and phone reboot. Determine exactly which of LTK/EDIV/RAND/session values persist and which are regenerated.

### D-PLAN-0004 — Basic semantic probes

One operation per capture: 5024 device information, 5023 battery, 5052 current time, notification subscribe/delivery. Use deliberate single changes (for example battery/time payload difference) to correlate bytes with semantics.

### D-PLAN-0005 — File/activity transfer

Create/identify one small known activity/health object. Trace file-type discovery and one download, then interrupt a larger transfer at controlled offsets. Verify 5002/5004/5054 fields, CRC checkpoints, compression, duplicate/retry/resume semantics and which file indexes represent required data on that watch.

---

## Error/recovery policy for the independent client

1. Never guess an unknown branch-controlling byte. Preserve it and fail with a semantic `unsupported/unknown protocol value` event.
2. Reject length, CRC, COBS and secure-wrapper violations before dispatching payload semantics.
3. Bound encoded packet length, aggregate notification payloads and file-transfer buffers.
4. Do not silently advance file offsets after mismatch; follow explicit retry/resume evidence.
5. Persist pairing secrets only in an OS-appropriate secure store and never emit them in normal logs.
6. Structured logs redact device unique identifiers by default.
7. Keep BLE transport, COBS, GFDI framing, secure session and feature codecs as separate layers so evidence can invalidate one layer without rewriting the rest.

---

## Automated test map

Current local tests are offline/static-proof tests and are not substitutes for target-watch integration.

| Test ID | File/test area | Facts |
|---|---|---|
| T-0001 | `tests/test_ble_features.py` write splitting | P-0002 |
| T-0002 | notification concatenation | P-0003 |
| T-0003/T-0004 | `tests/test_transport.py` discovery/lifecycle/failure | P-0001..P-0006 |
| T-0010 | COBS known vectors | P-0100/P-0101 |
| T-0011 | COBS property roundtrip | P-0101 |
| T-0020 | ordinary frame property roundtrip | P-0110 |
| T-0021 | transaction frame property roundtrip | P-0111 |
| T-0022 | CRC canonical/corruption tests | P-0112 |
| T-0023 | acknowledgement parse/build | P-0113 |
| T-0024 | `tests/test_link.py` request correlation/timeouts/unknowns | P-0114/P-0115 |
| T-0030 | generic XXTEA cross-check + property tests | P-0200 |
| T-0031 | reversed MAC block | P-0204 |
| T-0032 | confirm deterministic vector | P-0205 |
| T-0033 | STK deterministic vector | P-0206 |
| T-0034 | session-key deterministic vector | P-0208 |
| T-0035/T-0036 | secure-session roundtrip/negative checks | P-0210/P-0211 |
| T-0037 | `tests/test_session_state.py` state/persistence/timeout matrix | P-0212 |
| T-0038 | `tests/test_auth_protocol.py` full fresh + persistent auth simulation | P-0201..P-0212 |
| T-0039 | dual-pairing-gated 5112 OOB storage/negative tests | P-0203 |
| T-0040 | synthetic 5024 parse | P-0301 |
| T-0041 | battery parse/build | P-0310 |
| T-0042 | Garmin epoch/DST time response | P-0320 |
| T-0043 | `tests/test_handshake.py` host 5024 + 5050 handshake | P-0302/P-0303 |
| T-0044 | queued-download bitset roundtrip | P-0304 |
| T-0045 | `tests/test_sync.py` sync option/category bitsets | P-0330/P-0331 |
| T-0046 | File Ready fixed grammar/reserved-byte preservation | P-0332 |
| T-0047 | ordinary file receiver duplicate/offset/CRC tests | P-0402 |
| T-0048 | compressed receiver zlib/counter/CRC/failure tests | P-0405 |
| T-0049 | supported-file-type/directory parsing tests | P-0406/P-0407 |
| T-0055 | directory filter/archive/special-record tests | P-0407/P-0408 |
| T-0056 | `tests/test_file_client.py` read/cancel/fallback/listing simulation | P-0409/P-0410 |
| T-0057 | `tests/test_fit.py` FIT header/File-ID/activity-health classification | P-0411 |
| T-0050 | ANCS notification-source exact vector/property tests | P-0503 |
| T-0051/T-0052 | notification/app attribute request-response codecs | P-0504 |
| T-0053 | action/dismissal/control-point codecs | P-0505 |
| T-0054 | GNCS optional XXTEA + control-point semantic ACK/decrypt | P-0504/P-0506 |
| T-0058 | `tests/test_ancs.py` + `tests/test_client.py` application-owned attribute/action lifecycle | P-0507 |
| T-0060 | `tests/test_client.py` handshake/time/sync/GNCS orchestration | P-0301..P-0506 |
| T-0061 | `tests/test_protobuf_transport.py` + `tests/test_protobuf_link.py` chunk/reassembly/cancel/error simulation | P-0600 |
| T-0062 | `tests/test_protobuf_wire.py` bounded wire/envelope/capability vectors | P-0601..P-0603 |
| T-0063 | `tests/test_client_protobuf.py` semantic capability gating + connection-ready handling | P-0602..P-0604 |
| T-0064/T-0065 | `tests/test_file_access_proto.py` / `tests/test_file_access_client.py` FileAccess list/pull/status control | P-0700 |
| T-0066 | `tests/test_multilink.py` / `tests/test_multilink_client.py` MultiLink command/registration vectors | P-0701 |
| T-0067 | `tests/test_mlr.py` native-vector reliable header/fragmentation/ACK state | P-0702 |
| T-0068 | offline FileAccess + MultiLink/MLR download, zlib and cancel/recovery simulation | P-0704 |
| T-0070 | complete FileAccess service-schema/checksum vectors | P-0700/P-0705 |
| T-0071 | native MLR vectors, window growth, RTT/RTO and deferred ACK timing | P-0702/P-0703 |
| T-0072 | MLR timeout backoff, retransmission and out-of-order suppression | P-0703 |
| T-0073 | large >64-packet FileAccess transfer, resume, zlib and checksum integrity | P-0704..P-0706 |
| T-0074 | fresh-bond/persisted-reconnect policy plus persistent fitness-file synchronization | P-0213/P-0706 |

| T-0080 | `tests/test_fit.py` standard monitoring/HRV/stress/SpO2/sleep/respiration/body-battery projection | P-0412 |

| T-0081 | `tests/test_client_protobuf.py` peer bit-90 FileAccess gate vs optional bit-95 capabilities | P-0602/P-0700 |

| T-0082 | `tests/test_fit.py` Session/Lap/Activity aggregate semantics and sport/sub-sport mapping | P-0412 |

## Current blockers

Static reconstruction and offline implementation cover pairing, reconnect, legacy file transfer and FileAccess/MultiLink/MLR fitness extraction. Hardware acceptance is still pending because no target watch is attached: the remaining gate is fresh pairing/reconnect, live MultiLink registration, MLR timing/recovery, and representative activity/monitoring/sleep downloads on the designated watch.
