from __future__ import annotations

import asyncio

from garmin_proto_lab.auth_messages import derive_session_key
from garmin_proto_lab.auth_protocol import AuthProtocolEngine
from garmin_proto_lab.frame import Acknowledgement, Frame, ResponseStatus
from garmin_proto_lab.pairing import confirm_value, passkey_from_decimal, short_term_key
from garmin_proto_lab.session import MemoryPairingStore, PairingRecord, SessionPhase
from garmin_proto_lab.xxtea import decrypt as xxtea_decrypt, encrypt as xxtea_encrypt

DEVICE = "AA:BB:CC:DD:EE:FF"
ZERO_PASSKEY = bytes(16)
DEVICE_RANDOM = bytes(range(0x10, 0x20))
DEVICE_CONFIRM = confirm_value(DEVICE, ZERO_PASSKEY, DEVICE_RANDOM)
LTK = bytes(range(0x40, 0x50))
EDIV = b"\x01\x02"
RAND = bytes(range(0x50, 0x58))
DEVICE_SKD = bytes(range(0x60, 0x68))
HOST_SKD = bytes(range(0x70, 0x78))
HOST_RANDOM = bytes(range(0x80, 0x90))
HOST_IV = b"HIV4"
TAIL = b"TAIL"
DEVICE_IV = b"DIV4"


class FixedRandom:
    def __init__(self, *values: bytes) -> None:
        self.values = list(values)

    def __call__(self, size: int) -> bytes:
        assert self.values, f"unexpected random request size={size}"
        value = self.values.pop(0)
        assert len(value) == size
        return value


class FakeAuthLink:
    def __init__(self) -> None:
        self.responses: list[tuple[Frame, ResponseStatus | int, bytes]] = []
        self.requests: list[tuple[int, bytes]] = []
        self.secure: tuple[bytes, bytes, bytes] | None = None
        self.request_handler = None

    async def respond(self, frame, status=ResponseStatus.ACK, payload=b""):
        self.responses.append((frame, status, bytes(payload)))

    async def request(self, message_type, payload=b"", **kwargs):
        self.requests.append((message_type, bytes(payload)))
        if self.request_handler is None:
            body = b""
        else:
            body = self.request_handler(message_type, bytes(payload))
        return Acknowledgement(message_type, ResponseStatus.ACK, body, 0)

    def activate_secure_session(self, session_key, host_iv4, device_iv4):
        self.secure = (bytes(session_key), bytes(host_iv4), bytes(device_iv4))


def _fresh_request_handler(message_type: int, payload: bytes) -> bytes:
    if message_type == 5104:
        assert len(payload) == 16
        return b"\x00" + DEVICE_CONFIRM
    if message_type == 5105:
        assert payload == HOST_RANDOM
        return b"\x00" + DEVICE_RANDOM
    if message_type == 5106:
        assert payload == b"\x00"
        return b""
    raise AssertionError(f"unexpected request {message_type}")


def _verification_payload(session_key: bytes) -> bytes:
    return b"\x00" + xxtea_encrypt(session_key + bytes([0x99]) * 8, session_key)


def _secure_request(session_key: bytes) -> bytes:
    plain = bytearray(16)
    plain[0] = 7
    plain[8:12] = DEVICE_IV
    plain[12:16] = b"PEER"
    return xxtea_encrypt(bytes(plain), session_key)


def test_decimal_passkey_conversion_matches_signed_le_padding() -> None:
    assert passkey_from_decimal("123456") == (123456).to_bytes(4, "little", signed=True) + bytes(12)
    assert passkey_from_decimal("0") == bytes(16)


def test_full_fresh_just_works_pairing_to_secure_session() -> None:
    async def run() -> None:
        link = FakeAuthLink()
        link.request_handler = _fresh_request_handler
        store = MemoryPairingStore()
        engine = AuthProtocolEngine(
            link,  # type: ignore[arg-type]
            DEVICE,
            store,
            random_bytes=FixedRandom(HOST_RANDOM, HOST_SKD, HOST_IV, TAIL),
            monotonic=lambda: 1.0,
        )

        negotiation = bytes([0]) + (1).to_bytes(4, "little")
        assert await engine.handle(Frame(5101, negotiation, 1))
        response = link.responses[-1][2]
        assert response == bytes([0, 0]) + (1).to_bytes(4, "little")
        assert engine.state.phase is SessionPhase.WAITING_STK_BEGIN

        stk_begin = bytes([0]) + (30).to_bytes(2, "little") + b"\x00\x00" + b"\x01"
        assert await engine.handle(Frame(5103, stk_begin, 2))
        assert link.responses[-1][2] == b"\x00\xff"
        await engine.drain_background()
        expected_stk = short_term_key(ZERO_PASSKEY, HOST_RANDOM, DEVICE_RANDOM)
        assert engine.short_term_key == expected_stk
        assert engine.state.phase is SessionPhase.WAITING_LTK

        plain_ltk = LTK + EDIV + RAND + bytes(6)
        ltk_distribution = b"\x00" + xxtea_encrypt(plain_ltk, expected_stk)
        await engine.handle(Frame(5107, ltk_distribution, 3))
        assert store.load(DEVICE) == PairingRecord(LTK, EDIV, RAND)
        assert link.responses[-1][2] == b"\x00"
        assert engine.state.phase is SessionPhase.WAITING_SKD

        await engine.handle(Frame(5108, DEVICE_SKD, 4))
        assert link.responses[-1][2] == b"\x00" + HOST_SKD
        session_key = derive_session_key(LTK, DEVICE_SKD, HOST_SKD)
        assert engine.session_key == session_key
        assert engine.state.phase is SessionPhase.WAITING_VERIFICATION

        await engine.handle(Frame(5109, _verification_payload(session_key), 5))
        assert link.responses[-1][2] == b"\x00"
        assert engine.state.phase is SessionPhase.WAITING_SECURE_SESSION

        await engine.handle(Frame(5111, _secure_request(session_key), 6))
        assert engine.state.phase is SessionPhase.ESTABLISHED
        assert link.secure == (session_key, HOST_IV, DEVICE_IV)
        response_plain = xxtea_decrypt(link.responses[-1][2], session_key)
        assert response_plain[0] == 7
        assert response_plain[8:12] == HOST_IV
        assert response_plain[12:16] == TAIL
        assert all("secret redacted" in event.detail or not event.detail for event in engine.events if "ltk" in event.name or "session" in event.name)

    asyncio.run(run())


def test_persistent_ltk_reconnect_skips_stk_and_reuses_only_persistent_material() -> None:
    async def run() -> None:
        link = FakeAuthLink()

        def handler(message_type: int, payload: bytes) -> bytes:
            if message_type == 5102:
                assert payload == EDIV + RAND
                return b"\x00"
            raise AssertionError(f"unexpected request {message_type}")

        link.request_handler = handler
        store = MemoryPairingStore({DEVICE: PairingRecord(LTK, EDIV, RAND)})
        engine = AuthProtocolEngine(
            link,  # type: ignore[arg-type]
            DEVICE,
            store,
            random_bytes=FixedRandom(HOST_SKD, HOST_IV, TAIL),
            monotonic=lambda: 1.0,
        )
        negotiation = bytes([0]) + (1).to_bytes(4, "little")
        await engine.handle(Frame(5101, negotiation, 1))
        assert link.responses[-1][2][1] == 1
        await engine.drain_background()
        assert engine.state.phase is SessionPhase.WAITING_SKD
        assert engine.short_term_key is None

        await engine.handle(Frame(5108, DEVICE_SKD, 2))
        session_key = derive_session_key(LTK, DEVICE_SKD, HOST_SKD)
        await engine.handle(Frame(5109, _verification_payload(session_key), 3))
        await engine.handle(Frame(5111, _secure_request(session_key), 4))
        assert engine.state.phase is SessionPhase.ESTABLISHED
        assert link.secure == (session_key, HOST_IV, DEVICE_IV)

    asyncio.run(run())


def test_malformed_auth_request_gets_length_error_and_no_secret_log() -> None:
    async def run() -> None:
        link = FakeAuthLink()
        engine = AuthProtocolEngine(link, DEVICE, monotonic=lambda: 0.0)  # type: ignore[arg-type]
        await engine.handle(Frame(5101, b"bad", 1))
        assert link.responses[-1][1] is ResponseStatus.LENGTH_ERROR
        assert engine.state.phase is SessionPhase.FAILED
        assert not any(LTK.hex() in event.detail for event in engine.events)

    asyncio.run(run())


def test_oob_passkey_distribution_is_dual_pairing_gated_and_redacted() -> None:
    async def run() -> None:
        link = FakeAuthLink()
        saved: list[bytes] = []
        engine = AuthProtocolEngine(
            link,  # type: ignore[arg-type]
            DEVICE,
            dual_pairing=True,
            oob_passkey_sink=saved.append,
            monotonic=lambda: 0.0,
        )
        secret = bytes(range(16))
        await engine.handle(Frame(5112, secret + b"trailing", 1))
        assert link.responses[-1][1] is ResponseStatus.ACK
        assert link.responses[-1][2] == b"\x00"
        assert saved == [secret]
        assert engine._oob_passkey == secret
        assert all(secret.hex() not in event.detail for event in engine.events)

        nondual = AuthProtocolEngine(link, DEVICE, dual_pairing=False, monotonic=lambda: 0.0)  # type: ignore[arg-type]
        await nondual.handle(Frame(5112, secret, 2))
        assert link.responses[-1][2] == b"\x01"
        assert nondual._oob_passkey is None

        await nondual.handle(Frame(5112, b"short", 3))
        assert link.responses[-1][1] is ResponseStatus.LENGTH_ERROR

    asyncio.run(run())
