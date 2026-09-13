"""Live authentication/session protocol engine for GFDI messages 5101..5111.

The engine composes the independently implemented byte codecs and crypto
primitives. It does not own BLE transport or secret persistence; those are
provided by :class:`GfdiMessageLink` and :class:`PairingStore` respectively.
"""
from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from .auth_messages import (
    AuthCodecError,
    NegotiationRequest,
    NegotiationResponse,
    PasskeyMode,
    SessionKeyExchange,
    StkBeginRequest,
    StkBeginResponse,
    build_confirm_request,
    build_ltk_distribution_response,
    build_ltk_reconnect_request,
    build_random_request,
    build_secure_session_response,
    build_session_verification_response,
    build_stk_generation_status,
    decrypt_ltk_distribution,
    decrypt_secure_session_request,
    derive_session_key,
    parse_confirm_response,
    parse_random_response,
    verify_session_key_message,
)
from .frame import Frame, ResponseStatus
from .link import GfdiMessageLink
from .pairing import confirm_value, passkey_from_decimal, short_term_key
from .session import AuthSessionStateMachine, MemoryPairingStore, PairingRecord, PairingStore, SessionPhase

AUTH_IDS = frozenset({5101, 5103, 5107, 5108, 5109, 5111, 5112})
JUST_WORKS_PASSKEY = bytes(16)


class AuthProtocolError(RuntimeError):
    pass


PasskeyValue = bytes | str
PasskeyProvider = Callable[[PasskeyMode, int], PasskeyValue | Awaitable[PasskeyValue]]
RandomBytes = Callable[[int], bytes]
EstablishedCallback = Callable[["EstablishedSession"], object]
OobPasskeySink = Callable[[bytes], object]


@dataclass(frozen=True, slots=True)
class EstablishedSession:
    session_key: bytes
    device_iv4: bytes
    host_iv4: bytes


@dataclass(frozen=True, slots=True)
class AuthEvent:
    name: str
    detail: str = ""


@dataclass(slots=True)
class AuthProtocolEngine:
    link: GfdiMessageLink
    device_id: str
    store: PairingStore = field(default_factory=MemoryPairingStore)
    passkey_provider: PasskeyProvider | None = None
    random_bytes: RandomBytes = secrets.token_bytes
    monotonic: Callable[[], float] = time.monotonic
    on_established: EstablishedCallback | None = None
    dual_pairing: bool = False
    oob_passkey_sink: OobPasskeySink | None = None
    state: AuthSessionStateMachine = field(init=False)
    session_key: bytes | None = None
    short_term_key: bytes | None = None
    device_iv4: bytes | None = None
    host_iv4: bytes | None = None
    events: list[AuthEvent] = field(default_factory=list)
    _passkey: bytes | None = None
    _oob_passkey: bytes | None = None
    _tasks: set[asyncio.Task[object]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.state = AuthSessionStateMachine(self.device_id, self.store)

    def _event(self, name: str, detail: str = "") -> None:
        self.events.append(AuthEvent(name, detail))

    def _spawn(self, coro: Awaitable[object]) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain_background(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=False)

    async def handle(self, frame: Frame) -> bool:
        """Handle an inbound authentication request; return whether it was ours."""
        if frame.is_response or frame.message_type not in AUTH_IDS:
            return False
        now = self.monotonic()
        if self.state.check_timeout(now):
            self._event("timeout")
        try:
            if frame.message_type == 5101:
                await self._handle_negotiation(frame, now)
            elif frame.message_type == 5103:
                await self._handle_stk_begin(frame, now)
            elif frame.message_type == 5107:
                await self._handle_ltk_distribution(frame, now)
            elif frame.message_type == 5108:
                await self._handle_skd(frame, now)
            elif frame.message_type == 5109:
                await self._handle_verification(frame, now)
            elif frame.message_type == 5111:
                await self._handle_secure_session(frame, now)
            elif frame.message_type == 5112:
                await self._handle_oob_distribution(frame)
            return True
        except (AuthCodecError, ValueError) as exc:
            self.state.fail(str(exc))
            await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            self._event("malformed", f"{frame.message_type}: {exc}")
            return True

    def _restart_state_for_negotiation(self, now: float) -> None:
        if self.state.phase is not SessionPhase.WAITING_NEGOTIATION:
            self.state.reset()
            self.state.start(now)

    async def _handle_negotiation(self, frame: Frame, now: float) -> None:
        request = NegotiationRequest.parse(frame.payload)
        if self.state.phase in (SessionPhase.DISCONNECTED, SessionPhase.FAILED):
            self.state.start(now)
        else:
            self._restart_state_for_negotiation(now)
        record = self.store.load(self.device_id)
        response = NegotiationResponse.from_request(request, record is not None)
        self.state.receive_negotiation(now)
        await self.link.respond(frame, ResponseStatus.ACK, response.encode())
        self._event("5101_negotiation", "persistent" if record else "fresh")
        if response.common_algorithm_status != 0:
            self.state.fail("no common authentication algorithm")
            return
        if record is not None:
            self._spawn(self._attempt_ltk_reconnect(record))

    async def _attempt_ltk_reconnect(self, record: PairingRecord) -> None:
        ack = await self.link.request(
            5102,
            build_ltk_reconnect_request(record.encrypted_diversifier, record.random_number),
            timeout=30.0,
        )
        status = ack.payload[0] if ack.payload else 0xFF
        if status == 0:
            self.state.ltk_reconnect_result(True, self.monotonic())
            self._event("5102_reconnect_accepted")
            return
        self.state.ltk_reconnect_result(False, self.monotonic())
        self._event("5102_reconnect_rejected", str(status))
        await self.restart_authentication()

    async def restart_authentication(self) -> None:
        """Send the host-initiated 5101 restart request recovered statically."""
        ack = await self.link.request(5101, NegotiationRequest(0, 1).encode(), timeout=30.0)
        if not ack.payload or ack.payload[0] != 0:
            self.state.fail("restart authentication found no common algorithm")
            raise AuthProtocolError("restart authentication failed: no common algorithm")
        # The peer should next initiate 5103 STK generation.
        self.state.phase = SessionPhase.WAITING_STK_BEGIN
        self.state.deadline = self.monotonic() + 30.0
        self._event("5101_restart_accepted")

    async def _handle_stk_begin(self, frame: Frame, now: float) -> None:
        request = StkBeginRequest.parse(frame.payload)
        self.state.receive_stk_begin(request.mode, now, request.timeout_seconds)
        response = StkBeginResponse.for_request(request)
        await self.link.respond(frame, ResponseStatus.ACK, response.encode())
        self._event("5103_stk_begin", str(int(request.mode)))
        if not isinstance(request.mode, PasskeyMode):
            self.state.fail(f"unsupported passkey mode {int(request.mode)}")
            return
        if request.mode is PasskeyMode.JUST_WORKS:
            self._passkey = JUST_WORKS_PASSKEY
            self._spawn(self._run_fresh_exchange(JUST_WORKS_PASSKEY))
            return
        if request.mode is PasskeyMode.OUT_OF_BAND and self._oob_passkey is not None:
            passkey = self._oob_passkey
            self.state.provide_passkey(passkey, self.monotonic())
            self._passkey = passkey
            self._spawn(self._run_fresh_exchange(passkey))
            return
        self._spawn(self._obtain_passkey_and_exchange(request.mode, request.effective_timeout_seconds))

    async def _obtain_passkey_and_exchange(self, mode: PasskeyMode, timeout_seconds: int) -> None:
        provider = self.passkey_provider
        if provider is None:
            self.state.fail("passkey required but no provider configured")
            raise AuthProtocolError("passkey required but no passkey provider configured")
        result = provider(mode, timeout_seconds)
        try:
            value = await asyncio.wait_for(result, timeout_seconds) if inspect.isawaitable(result) else result
        except asyncio.TimeoutError as exc:
            self.state.fail("passkey timeout")
            raise AuthProtocolError("timed out waiting for pairing passkey") from exc
        passkey = passkey_from_decimal(value) if isinstance(value, str) else bytes(value)
        if len(passkey) != 16:
            self.state.fail("invalid passkey length")
            raise AuthProtocolError("pairing passkey must resolve to exactly 16 bytes")
        self.state.provide_passkey(passkey, self.monotonic())
        self._passkey = passkey
        await self._run_fresh_exchange(passkey)

    async def _run_fresh_exchange(self, passkey: bytes) -> None:
        if self.state.phase is not SessionPhase.STK_EXCHANGE:
            # JUST_WORKS transitions to STK_EXCHANGE in receive_stk_begin;
            # visible/OOB does so in provide_passkey.
            raise AuthProtocolError(f"fresh key exchange invalid in {self.state.phase.value}")
        host_random = self.random_bytes(16)
        if len(host_random) != 16:
            raise AuthProtocolError("random source did not return 16 bytes")
        host_confirm = confirm_value(self.device_id, passkey, host_random)

        confirm_ack = await self.link.request(5104, build_confirm_request(host_confirm), timeout=30.0)
        peer_confirm_response = parse_confirm_response(confirm_ack.payload)
        if peer_confirm_response.status != 0 or peer_confirm_response.value16 is None:
            self.state.fail(f"peer confirm request failed with status {peer_confirm_response.status}")
            raise AuthProtocolError("peer confirm request failed")
        device_confirm = peer_confirm_response.value16

        random_ack = await self.link.request(5105, build_random_request(host_random), timeout=30.0)
        peer_random_response = parse_random_response(random_ack.payload)
        if peer_random_response.status == 2:
            self.state.stk_exchange_result(False, self.monotonic())
            self._event("peer_rejected_confirm")
            await self.restart_authentication()
            return
        if peer_random_response.status != 0 or peer_random_response.value16 is None:
            self.state.fail(f"peer random request failed with status {peer_random_response.status}")
            raise AuthProtocolError("peer random request failed")
        device_random = peer_random_response.value16
        matches = confirm_value(self.device_id, passkey, device_random) == device_confirm
        if matches:
            self.short_term_key = short_term_key(passkey, host_random, device_random)
        await self.link.request(5106, build_stk_generation_status(matches), timeout=30.0)
        actions = self.state.stk_exchange_result(matches, self.monotonic())
        self._event("5106_stk_status", "success" if matches else "confirm_mismatch")
        if not matches:
            if actions:
                await self.restart_authentication()
            return

    async def _handle_ltk_distribution(self, frame: Frame, now: float) -> None:
        stk = self.short_term_key
        if stk is None:
            await self.link.respond(frame, ResponseStatus.ACK, build_ltk_distribution_response(False))
            self.state.fail("LTK distribution received without STK")
            self._event("5107_without_stk")
            return
        material = decrypt_ltk_distribution(frame.payload, stk)
        record = PairingRecord(material.long_term_key, material.encrypted_diversifier, material.random_number)
        self.state.receive_ltk_distribution(record, now)
        await self.link.respond(frame, ResponseStatus.ACK, build_ltk_distribution_response(True))
        self._event("5107_ltk_saved", "secret redacted")

    async def _handle_skd(self, frame: Frame, now: float) -> None:
        exchange = SessionKeyExchange.parse_request(frame.payload)
        record = self.store.load(self.device_id)
        if record is None:
            self.state.fail("5108 received with no persistent LTK")
            await self.link.respond(frame, ResponseStatus.ACK, b"\x01")
            self._event("5108_without_ltk")
            return
        host_skd8 = self.random_bytes(8)
        if len(host_skd8) != 8:
            raise AuthProtocolError("random source did not return 8-byte host SKD")
        self.session_key = derive_session_key(record.long_term_key, exchange.device_skd8, host_skd8)
        self.state.receive_skd(now)
        await self.link.respond(frame, ResponseStatus.ACK, exchange.build_response(host_skd8))
        self._event("5108_session_key_derived", "secret redacted")

    async def _handle_verification(self, frame: Frame, now: float) -> None:
        key = self.session_key
        if key is None:
            await self.link.respond(frame, ResponseStatus.ACK, build_session_verification_response(False, False))
            self.state.fail("5109 received without session key")
            self._event("5109_without_session_key")
            return
        _parsed, matches = verify_session_key_message(frame.payload, key)
        self.state.receive_verification(matches, now)
        await self.link.respond(frame, ResponseStatus.ACK, build_session_verification_response(matches))
        self._event("5109_verification", "match" if matches else "mismatch")

    async def _handle_oob_distribution(self, frame: Frame) -> None:
        if len(frame.payload) < 16:
            await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            self._event("5112_malformed")
            return
        if not self.dual_pairing:
            await self.link.respond(frame, ResponseStatus.ACK, b"\x01")
            self._event("5112_rejected_not_dual_pairing")
            return
        passkey = bytes(frame.payload[:16])
        self._oob_passkey = passkey
        await self.link.respond(frame, ResponseStatus.ACK, b"\x00")
        sink = self.oob_passkey_sink
        if sink is not None:
            result = sink(passkey)
            if inspect.isawaitable(result):
                await result
        self._event("5112_oob_passkey_saved", "secret redacted")

    async def _handle_secure_session(self, frame: Frame, now: float) -> None:
        key = self.session_key
        if key is None:
            self.state.fail("5111 received without session key")
            await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            self._event("5111_without_session_key")
            return
        request = decrypt_secure_session_request(frame.payload, key)
        host_iv4 = self.random_bytes(4)
        random_tail4 = self.random_bytes(4)
        if len(host_iv4) != 4 or len(random_tail4) != 4:
            raise AuthProtocolError("random source did not return four-byte secure-session values")
        response = build_secure_session_response(request, key, host_iv4, random_tail4)
        # Critical ordering: the 5111 acknowledgement is still a plaintext GFDI
        # frame. Only after it is written do subsequent packets use the wrapper.
        await self.link.respond(frame, ResponseStatus.ACK, response)
        actions = self.state.receive_secure_session(now)
        self.device_iv4 = request.device_iv4
        self.host_iv4 = host_iv4
        self.link.activate_secure_session(key, host_iv4, request.device_iv4)
        established = EstablishedSession(key, request.device_iv4, host_iv4)
        self._event("5111_secure_session_established", "secret redacted")
        callback = self.on_established
        if callback is not None:
            result = callback(established)
            if inspect.isawaitable(result):
                await result
        if not actions:
            raise AuthProtocolError("session state failed to mark secure session established")
