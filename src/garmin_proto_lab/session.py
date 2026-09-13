"""Pure authentication/session state machine for the GFDI 5101..5111 path.

This module captures ordering, timeouts, persistence boundaries and failure
semantics. Cryptographic transforms live in :mod:`auth_messages` and
:mod:`pairing`; BLE I/O lives in :mod:`transport`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
from enum import Enum
from typing import Protocol

from .auth_messages import PasskeyMode

DEFAULT_AUTH_TIMEOUT_SECONDS = 30.0


class SessionError(RuntimeError):
    pass


class SessionPhase(str, Enum):
    DISCONNECTED = "disconnected"
    WAITING_NEGOTIATION = "waiting_negotiation"
    WAITING_STK_BEGIN = "waiting_stk_begin"
    WAITING_PASSKEY = "waiting_passkey"
    STK_EXCHANGE = "stk_exchange"
    WAITING_LTK = "waiting_ltk"
    RECONNECTING_LTK = "reconnecting_ltk"
    WAITING_SKD = "waiting_skd"
    WAITING_VERIFICATION = "waiting_verification"
    WAITING_SECURE_SESSION = "waiting_secure_session"
    ESTABLISHED = "established"
    FAILED = "failed"


class SessionAction(str, Enum):
    RESPOND_NEGOTIATION = "respond_negotiation"
    SEND_LTK_RECONNECT = "send_ltk_reconnect"
    RESPOND_STK_BEGIN = "respond_stk_begin"
    REQUEST_VISIBLE_PASSKEY = "request_visible_passkey"
    REQUEST_OOB_PASSKEY = "request_oob_passkey"
    START_STK_EXCHANGE = "start_stk_exchange"
    ACK_LTK_DISTRIBUTION = "ack_ltk_distribution"
    RESPOND_SKD = "respond_skd"
    RESPOND_SESSION_VERIFICATION = "respond_session_verification"
    RESPOND_SECURE_SESSION = "respond_secure_session"
    RESTART_AUTH = "restart_auth"
    SESSION_ESTABLISHED = "session_established"


@dataclass(frozen=True, slots=True)
class PairingRecord:
    """Persistent authentication material only.

    Session keys, IVs and packet counters are deliberately absent because
    static evidence regenerates them for each secure session.
    """

    long_term_key: bytes
    encrypted_diversifier: bytes
    random_number: bytes

    def __post_init__(self) -> None:
        if len(self.long_term_key) != 16:
            raise ValueError("long_term_key must be 16 bytes")
        if len(self.encrypted_diversifier) != 2:
            raise ValueError("encrypted_diversifier must be 2 bytes")
        if len(self.random_number) != 8:
            raise ValueError("random_number must be 8 bytes")


class PairingStore(Protocol):
    def load(self, device_id: str) -> PairingRecord | None: ...
    def save(self, device_id: str, record: PairingRecord) -> None: ...
    def delete(self, device_id: str) -> None: ...


@dataclass(slots=True)
class MemoryPairingStore:
    """Non-persistent test/reference store; never serializes secrets to disk."""

    records: dict[str, PairingRecord] = field(default_factory=dict)

    def load(self, device_id: str) -> PairingRecord | None:
        return self.records.get(device_id)

    def save(self, device_id: str, record: PairingRecord) -> None:
        self.records[device_id] = record

    def delete(self, device_id: str) -> None:
        self.records.pop(device_id, None)


@dataclass(slots=True)
class FilePairingStore:
    """Small lab/reference persistent store with strict filesystem permissions.

    Device identifiers are hashed before writing. Secret bytes are never logged.
    This is suitable for a single-user interoperability lab; production clients
    should replace it with the platform credential/keystore facility.
    """

    path: Path

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()

    @staticmethod
    def _key(device_id: str) -> str:
        return hashlib.sha256(device_id.encode("utf-8")).hexdigest()

    def _read(self) -> dict[str, object]:
        if not self.path.exists():
            return {"version": 1, "records": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError("pairing store cannot be read") from exc
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("records"), dict):
            raise SessionError("pairing store has an unsupported format")
        return data

    def _write(self, data: dict[str, object]) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(parent, 0o700)
        except OSError:
            pass
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name + ".", dir=parent, text=True)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass

    def load(self, device_id: str) -> PairingRecord | None:
        data = self._read()
        raw = data["records"].get(self._key(device_id))  # type: ignore[index,union-attr]
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise SessionError("pairing record is malformed")
        try:
            return PairingRecord(
                bytes.fromhex(str(raw["ltk"])),
                bytes.fromhex(str(raw["ediv"])),
                bytes.fromhex(str(raw["rand"])),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise SessionError("pairing record is malformed") from exc

    def save(self, device_id: str, record: PairingRecord) -> None:
        data = self._read()
        records = data["records"]
        assert isinstance(records, dict)
        existing = records.get(self._key(device_id))
        oob = existing.get("oob") if isinstance(existing, dict) else None
        value: dict[str, str] = {
            "ltk": record.long_term_key.hex(),
            "ediv": record.encrypted_diversifier.hex(),
            "rand": record.random_number.hex(),
        }
        if isinstance(oob, str):
            value["oob"] = oob
        records[self._key(device_id)] = value
        self._write(data)

    def delete(self, device_id: str) -> None:
        data = self._read()
        records = data["records"]
        assert isinstance(records, dict)
        records.pop(self._key(device_id), None)
        self._write(data)

    def load_oob(self, device_id: str) -> bytes | None:
        data = self._read()
        records = data["records"]
        assert isinstance(records, dict)
        raw = records.get(self._key(device_id))
        if not isinstance(raw, dict) or not isinstance(raw.get("oob"), str):
            return None
        try:
            value = bytes.fromhex(raw["oob"])
        except ValueError as exc:
            raise SessionError("stored OOB passkey is malformed") from exc
        if len(value) != 16:
            raise SessionError("stored OOB passkey has invalid length")
        return value

    def save_oob(self, device_id: str, passkey: bytes) -> None:
        if len(passkey) != 16:
            raise SessionError("OOB passkey must be 16 bytes")
        data = self._read()
        records = data["records"]
        assert isinstance(records, dict)
        key = self._key(device_id)
        existing = records.get(key)
        value = dict(existing) if isinstance(existing, dict) else {}
        value["oob"] = passkey.hex()
        records[key] = value
        self._write(data)


@dataclass(frozen=True, slots=True)
class SessionEvent:
    phase: SessionPhase
    name: str
    detail: str = ""


@dataclass(slots=True)
class AuthSessionStateMachine:
    device_id: str
    store: PairingStore
    phase: SessionPhase = SessionPhase.DISCONNECTED
    deadline: float | None = None
    passkey_mode: PasskeyMode | None = None
    events: list[SessionEvent] = field(default_factory=list)

    @property
    def persistent_record(self) -> PairingRecord | None:
        return self.store.load(self.device_id)

    @property
    def is_established(self) -> bool:
        return self.phase is SessionPhase.ESTABLISHED

    def _event(self, name: str, detail: str = "") -> None:
        self.events.append(SessionEvent(self.phase, name, detail))

    def _deadline(self, now: float, seconds: float = DEFAULT_AUTH_TIMEOUT_SECONDS) -> None:
        self.deadline = now + seconds

    def start(self, now: float) -> None:
        if self.phase not in (SessionPhase.DISCONNECTED, SessionPhase.FAILED):
            raise SessionError(f"cannot start authentication from {self.phase.value}")
        self.phase = SessionPhase.WAITING_NEGOTIATION
        self.passkey_mode = None
        self._deadline(now)
        self._event("auth_started")

    def reset(self) -> None:
        self.phase = SessionPhase.DISCONNECTED
        self.deadline = None
        self.passkey_mode = None
        self._event("reset")

    def fail(self, reason: str) -> None:
        self.phase = SessionPhase.FAILED
        self.deadline = None
        self._event("failed", reason)

    def check_timeout(self, now: float) -> bool:
        if self.deadline is not None and now >= self.deadline and self.phase not in (
            SessionPhase.ESTABLISHED,
            SessionPhase.DISCONNECTED,
            SessionPhase.FAILED,
        ):
            self.fail("authentication timeout")
            return True
        return False

    def receive_negotiation(self, now: float) -> tuple[SessionAction, ...]:
        if self.phase not in (SessionPhase.WAITING_NEGOTIATION, SessionPhase.RECONNECTING_LTK):
            raise SessionError(f"5101 negotiation is invalid in {self.phase.value}")
        self._deadline(now)
        self._event("5101_negotiation")
        if self.persistent_record is not None:
            self.phase = SessionPhase.RECONNECTING_LTK
            return (SessionAction.RESPOND_NEGOTIATION, SessionAction.SEND_LTK_RECONNECT)
        self.phase = SessionPhase.WAITING_STK_BEGIN
        return (SessionAction.RESPOND_NEGOTIATION,)

    def ltk_reconnect_result(self, accepted: bool, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.RECONNECTING_LTK:
            raise SessionError(f"LTK reconnect result is invalid in {self.phase.value}")
        self._deadline(now)
        if accepted:
            self.phase = SessionPhase.WAITING_SKD
            self._event("ltk_reconnect_accepted")
            return ()
        self.phase = SessionPhase.WAITING_NEGOTIATION
        self._event("ltk_reconnect_rejected")
        return (SessionAction.RESTART_AUTH,)

    def receive_stk_begin(
        self,
        mode: PasskeyMode | int,
        now: float,
        timeout_seconds: int = 0,
    ) -> tuple[SessionAction, ...]:
        if self.phase not in (SessionPhase.WAITING_STK_BEGIN, SessionPhase.RECONNECTING_LTK):
            raise SessionError(f"5103 STK begin is invalid in {self.phase.value}")
        try:
            parsed_mode = PasskeyMode(int(mode))
        except ValueError as exc:
            self.fail(f"unsupported passkey mode {int(mode)}")
            raise SessionError(f"unsupported passkey mode {int(mode)}") from exc
        self.passkey_mode = parsed_mode
        effective_timeout = float(timeout_seconds or 30)
        self._deadline(now, effective_timeout)
        self._event("5103_stk_begin", parsed_mode.name.lower())
        if parsed_mode is PasskeyMode.VISIBLE:
            self.phase = SessionPhase.WAITING_PASSKEY
            return (SessionAction.RESPOND_STK_BEGIN, SessionAction.REQUEST_VISIBLE_PASSKEY)
        if parsed_mode is PasskeyMode.OUT_OF_BAND:
            self.phase = SessionPhase.WAITING_PASSKEY
            return (SessionAction.RESPOND_STK_BEGIN, SessionAction.REQUEST_OOB_PASSKEY)
        self.phase = SessionPhase.STK_EXCHANGE
        return (SessionAction.RESPOND_STK_BEGIN, SessionAction.START_STK_EXCHANGE)

    def provide_passkey(self, passkey: bytes, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.WAITING_PASSKEY:
            raise SessionError(f"passkey is invalid in {self.phase.value}")
        if len(passkey) != 16:
            raise SessionError("passkey must be exactly 16 bytes")
        self.phase = SessionPhase.STK_EXCHANGE
        self._deadline(now)
        self._event("passkey_supplied", "redacted")
        return (SessionAction.START_STK_EXCHANGE,)

    def stk_exchange_result(self, verified: bool, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.STK_EXCHANGE:
            raise SessionError(f"STK exchange result is invalid in {self.phase.value}")
        self._deadline(now)
        if not verified:
            self.phase = SessionPhase.WAITING_NEGOTIATION
            self._event("stk_confirm_mismatch")
            return (SessionAction.RESTART_AUTH,)
        self.phase = SessionPhase.WAITING_LTK
        self._event("stk_established")
        return ()

    def receive_ltk_distribution(self, record: PairingRecord, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.WAITING_LTK:
            raise SessionError(f"5107 LTK distribution is invalid in {self.phase.value}")
        self.store.save(self.device_id, record)
        self.phase = SessionPhase.WAITING_SKD
        self._deadline(now)
        self._event("5107_ltk_saved", "secret redacted")
        return (SessionAction.ACK_LTK_DISTRIBUTION,)

    def receive_skd(self, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.WAITING_SKD:
            raise SessionError(f"5108 SKD distribution is invalid in {self.phase.value}")
        if self.persistent_record is None:
            self.fail("session SKD received without persistent LTK")
            raise SessionError("session SKD received without persistent LTK")
        self.phase = SessionPhase.WAITING_VERIFICATION
        self._deadline(now)
        self._event("5108_skd")
        return (SessionAction.RESPOND_SKD,)

    def receive_verification(self, matches: bool, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.WAITING_VERIFICATION:
            raise SessionError(f"5109 verification is invalid in {self.phase.value}")
        self._deadline(now)
        self._event("5109_verification", "match" if matches else "mismatch")
        if not matches:
            self.phase = SessionPhase.WAITING_NEGOTIATION
            return (SessionAction.RESPOND_SESSION_VERIFICATION, SessionAction.RESTART_AUTH)
        self.phase = SessionPhase.WAITING_SECURE_SESSION
        return (SessionAction.RESPOND_SESSION_VERIFICATION,)

    def receive_secure_session(self, now: float) -> tuple[SessionAction, ...]:
        if self.phase is not SessionPhase.WAITING_SECURE_SESSION:
            raise SessionError(f"5111 secure-session init is invalid in {self.phase.value}")
        self.phase = SessionPhase.ESTABLISHED
        self.deadline = None
        self._event("5111_secure_session")
        return (SessionAction.RESPOND_SECURE_SESSION, SessionAction.SESSION_ESTABLISHED)
