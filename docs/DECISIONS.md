# Project decisions

## 2026-09-13 — ADR-0001: public interoperability sources are allowed

The project scope now permits public open-source Garmin interoperability implementations as research and implementation references. The earlier rule excluding third-party implementations until the completion gate is removed.

This changes the research-input policy, not the runtime goal: the project still must operate without Garmin Connect, proprietary Garmin libraries, account tokens, decompiled Garmin source, or bundled Garmin binaries. Any third-party code actually incorporated into this repository must be license-compatible and carry the required attribution/notices. Protocol discrepancies are resolved against the Garmin APK/native evidence and, when available, owner-device captures.

During the current pass, `wh1le/garmin-bridge` was reviewed at its current public revision as a cross-reference. It is AGPL-3.0 licensed. No source from that repository was copied into `garmin-proto-lab`; the pairing, GFDI, FileAccess, MultiLink and MLR code in this repository remains the code already developed here.


## 2026-09-13 — ADR-0002: public FIT profile is a protocol reference

The public `garmin/fit-python-sdk` repository at revision `f0d86b18195dbdf9c5b2f135aad5d6ae541f5fbb` reports FIT Profile 21.214.0 and is used to cross-check standard FIT message names, field numbers, scales and enum values. No SDK code is vendored or imported by `garmin-proto-lab`; the project keeps its own small FIT container/record decoder and preserves raw fields it does not semantically project.
