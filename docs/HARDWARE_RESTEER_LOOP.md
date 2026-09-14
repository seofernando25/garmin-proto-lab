# Hardware verification resteering loop

This is the behavior loop for sessions where a physical Garmin watch is available. Its purpose is to prevent long, ambiguous test runs and to convert every failure into a small next experiment.

## Core rule

Run **one controlled state transition at a time**. Capture it, compare it with the current model, classify the first divergence, and change only the layer responsible for that divergence.

Do not continue deeper into the workflow after an earlier layer has failed. A failed FileAccess transfer is not actionable if pairing or the selected physical GATT route is already wrong.

## Session bootstrap

At the start of a hardware session:

1. `git status --short`; start from a known commit.
2. Record watch model + firmware and host OS/tool versions in local notes. Do not commit serial/MAC/account identifiers.
3. Stop/disable Garmin Connect for independent-client acceptance runs.
4. Start a fresh `btmon` capture for exactly one experiment.
5. Use a descriptive local capture name, but keep it under ignored `captures/`.
6. Run the smallest CLI command that exercises the layer under test.

## Layered verification order

Use this order unless existing evidence already proves an earlier layer on the same watch/firmware:

```text
L0 discovery
L1 GATT route + MTU + subscriptions
L2 GFDI framing/handshake/configuration
L3 pairing/authentication
L4 reconnect/persistence
L5 capability selection
L6 file listing/control plane
L7 MultiLink/MLR data plane
L8 FIT validation/decoding
L9 recovery and final Garmin-Connect-free acceptance
```

## The resteering cycle

After each experiment:

### 1. Observe

Write down only concrete facts:

- command and starting state;
- selected service/characteristics;
- MTU/subscription order;
- first unexpected frame/state/error;
- direction and message/handle IDs;
- relevant lengths/status codes;
- whether the watch UI prompted or changed state.

### 2. Locate the first divergence

Compare the run with `spec/PROTOCOL.md`. Find the **earliest** point where observed behavior differs.

Examples:

- no device found -> discovery/filter problem;
- connect succeeds but expected GFDI pair absent -> route selection/MultiLink problem;
- 5024/5050 ordering differs -> handshake state-machine problem;
- bond exists but 5101 arrives -> pairing-route assumption problem;
- peer bit 90 present but listing fails -> FileAccess control-plane problem;
- FileAccess pull succeeds but bytes stall -> MultiLink/MLR problem;
- bytes complete but FIT CRC fails -> transfer integrity or file-selection problem;
- valid FIT but semantic values wrong -> FIT decoder/profile problem.

### 3. Classify the mismatch

Use one category:

- **environment** — adapter/BlueZ/permissions/watch not in expected mode;
- **missing protocol branch** — valid device behavior not modeled;
- **wrong protocol fact** — our documented field/state assumption is incorrect;
- **timing/retry** — ordering is right but timeout/window/retry differs;
- **device capability variance** — operation is unsupported or routed differently on this model;
- **implementation bug** — spec is right, code is wrong;
- **decoder gap** — transfer is good, data semantics are incomplete.

### 4. Reduce to the next smallest experiment

Do not immediately rerun full `fitness-sync`. Choose a probe that distinguishes two explanations.

Examples:

- run `services` before `probe` to distinguish service selection from handshake failure;
- run `multilink-info` to verify service 1/4 before opening FileAccess;
- run `pair` separately from `fitness-sync` to isolate authentication;
- list FileAccess items without downloading to isolate control plane;
- download one small object before testing resume/compression;
- disable compression before diagnosing MLR framing;
- decode the captured GFDI frame offline before changing the live client.

### 5. Update evidence first

Create a sanitized `D-####` observation with the minimal relevant facts. Link it to existing `S-####`/`P-####` entries or create/update the protocol statement if the observation disproves it.

Do not commit secrets or full raw captures.

### 6. Change spec/code/test together

If code needs to change:

1. update the protocol statement;
2. create a sanitized fixture or synthetic reproduction;
3. add the smallest failing test;
4. implement the correction;
5. run the focused test, full suite, and offline gate.

Then repeat the same hardware experiment before advancing layers.

## Pass criteria by stage

A layer is considered verified only when the same behavior is repeatable and the evidence can be tied to a requirement.

- **Discovery/GATT:** services, characteristic properties, MTU, subscriptions, and selected route recorded.
- **Pairing:** clean state succeeds with the intended route and persistent state outcome is known.
- **Reconnect:** client restart + Bluetooth toggle + watch reboot + host reboot behavior is characterized.
- **FileAccess:** item listing is real, not synthetic; one object is selected by actual peer metadata.
- **MLR:** sustained transfer works, sequence/ACK behavior is coherent, and interruption resumes correctly.
- **FIT:** downloaded object passes checksum/FIT CRC and semantic decoding produces plausible values while retaining raw fields.
- **Acceptance:** full workflow succeeds with Garmin Connect stopped/absent.

## Failure stop conditions

Stop advancing and resteer when any of these occur:

- repeated timeout at the same state;
- watch unexpectedly unpairs/reboots;
- selected route changes between nominally identical runs;
- unknown status/enum controls a branch;
- integrity/checksum mismatch;
- data length disagrees with negotiated/listed length;
- a workaround would require guessing protocol bytes;
- a test would risk deleting user data or modifying unrelated watch state.

The correct response is a narrower experiment, not more retries.

## Recovery experiments

Only after a normal end-to-end sync passes, test one fault at a time:

1. client process restart;
2. Bluetooth off/on;
3. watch reboot;
4. host reboot;
5. out-of-range disconnect/reconnect;
6. deliberate request timeout;
7. interrupted MLR/FileAccess transfer and `.part` resume;
8. compressed transfer where supported;
9. legacy fallback where supported.

Reset back to a known baseline between experiments.

## What to report at the end of a session

Summarize:

- last known-good layer;
- first unresolved divergence;
- exact requirement(s) now verified;
- new `D-####` evidence IDs;
- code/spec/tests changed;
- next smallest experiment.

This makes the next session restartable without rereading raw captures or repeating already-settled experiments.
