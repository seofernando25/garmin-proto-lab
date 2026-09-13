from __future__ import annotations

import pytest

from garmin_proto_lab.auth_messages import PasskeyMode
from garmin_proto_lab.session import (
    AuthSessionStateMachine,
    MemoryPairingStore,
    PairingRecord,
    SessionAction,
    SessionError,
    SessionPhase,
)

RECORD = PairingRecord(bytes(range(16)), b"\x12\x34", bytes(range(8)))


def test_fresh_visible_pairing_state_path() -> None:
    store = MemoryPairingStore()
    session = AuthSessionStateMachine("device-a", store)
    session.start(0)
    assert session.phase is SessionPhase.WAITING_NEGOTIATION
    assert session.receive_negotiation(1) == (SessionAction.RESPOND_NEGOTIATION,)
    assert session.phase is SessionPhase.WAITING_STK_BEGIN

    actions = session.receive_stk_begin(PasskeyMode.VISIBLE, now=2, timeout_seconds=45)
    assert actions == (SessionAction.RESPOND_STK_BEGIN, SessionAction.REQUEST_VISIBLE_PASSKEY)
    assert session.phase is SessionPhase.WAITING_PASSKEY
    assert session.deadline == 47

    assert session.provide_passkey(bytes(16), now=3) == (SessionAction.START_STK_EXCHANGE,)
    assert session.stk_exchange_result(True, now=4) == ()
    assert session.phase is SessionPhase.WAITING_LTK

    assert session.receive_ltk_distribution(RECORD, now=5) == (SessionAction.ACK_LTK_DISTRIBUTION,)
    assert store.load("device-a") == RECORD
    assert session.receive_skd(now=6) == (SessionAction.RESPOND_SKD,)
    assert session.receive_verification(True, now=7) == (SessionAction.RESPOND_SESSION_VERIFICATION,)
    actions = session.receive_secure_session(now=8)
    assert actions == (SessionAction.RESPOND_SECURE_SESSION, SessionAction.SESSION_ESTABLISHED)
    assert session.phase is SessionPhase.ESTABLISHED
    assert session.deadline is None
    assert not any(RECORD.long_term_key.hex() in event.detail for event in session.events)


def test_just_works_skips_user_wait() -> None:
    session = AuthSessionStateMachine("device-a", MemoryPairingStore())
    session.start(0)
    session.receive_negotiation(1)
    actions = session.receive_stk_begin(PasskeyMode.JUST_WORKS, now=2)
    assert actions == (SessionAction.RESPOND_STK_BEGIN, SessionAction.START_STK_EXCHANGE)
    assert session.phase is SessionPhase.STK_EXCHANGE


def test_persistent_reconnect_uses_ltk_and_regenerates_session_state() -> None:
    store = MemoryPairingStore({"device-a": RECORD})
    session = AuthSessionStateMachine("device-a", store)
    session.start(0)
    actions = session.receive_negotiation(1)
    assert actions == (SessionAction.RESPOND_NEGOTIATION, SessionAction.SEND_LTK_RECONNECT)
    assert session.phase is SessionPhase.RECONNECTING_LTK
    assert session.ltk_reconnect_result(True, now=2) == ()
    assert session.phase is SessionPhase.WAITING_SKD
    session.receive_skd(now=3)
    session.receive_verification(True, now=4)
    session.receive_secure_session(now=5)
    assert session.is_established


def test_rejected_reconnect_and_confirm_mismatch_restart_auth() -> None:
    store = MemoryPairingStore({"device-a": RECORD})
    session = AuthSessionStateMachine("device-a", store)
    session.start(0)
    session.receive_negotiation(1)
    assert session.ltk_reconnect_result(False, 2) == (SessionAction.RESTART_AUTH,)
    assert session.phase is SessionPhase.WAITING_NEGOTIATION

    store.delete("device-a")
    session.receive_negotiation(3)
    session.receive_stk_begin(PasskeyMode.JUST_WORKS, 4)
    assert session.stk_exchange_result(False, 5) == (SessionAction.RESTART_AUTH,)
    assert session.phase is SessionPhase.WAITING_NEGOTIATION


def test_wrong_state_unsupported_mode_timeout_and_reset_fail_safely() -> None:
    session = AuthSessionStateMachine("device-a", MemoryPairingStore())
    with pytest.raises(SessionError, match="invalid"):
        session.receive_skd(0)

    session.start(10)
    session.receive_negotiation(11)
    with pytest.raises(SessionError, match="unsupported passkey mode"):
        session.receive_stk_begin(99, 12)
    assert session.phase is SessionPhase.FAILED

    session.start(20)
    assert session.check_timeout(49.9) is False
    assert session.check_timeout(50.0) is True
    assert session.phase is SessionPhase.FAILED
    session.reset()
    assert session.phase is SessionPhase.DISCONNECTED
    assert session.deadline is None


def test_session_rejects_secret_shape_errors() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        PairingRecord(b"short", b"\x00\x00", bytes(8))
    session = AuthSessionStateMachine("device-a", MemoryPairingStore())
    session.start(0)
    session.receive_negotiation(1)
    session.receive_stk_begin(PasskeyMode.VISIBLE, 2)
    with pytest.raises(SessionError, match="16 bytes"):
        session.provide_passkey(b"1234", 3)


def test_file_pairing_store_persists_hashed_device_key_with_strict_mode(tmp_path) -> None:
    import json
    import stat

    from garmin_proto_lab.session import FilePairingStore

    path = tmp_path / "secrets" / "pairing.json"
    store = FilePairingStore(path)
    store.save("AA:BB:CC:DD:EE:FF", RECORD)
    assert store.load("AA:BB:CC:DD:EE:FF") == RECORD
    raw = path.read_text()
    assert "AA:BB:CC:DD:EE:FF" not in raw
    assert RECORD.long_term_key.hex() in raw
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    oob = bytes(range(16))
    store.save_oob("11:22:33:44:55:66", oob)
    assert store.load_oob("11:22:33:44:55:66") == oob
    assert "11:22:33:44:55:66" not in path.read_text()

    store.delete("AA:BB:CC:DD:EE:FF")
    assert store.load("AA:BB:CC:DD:EE:FF") is None
