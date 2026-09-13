"""Composition of COBS, optional secure wrapping, and the GFDI frame codec."""
from __future__ import annotations

from dataclasses import dataclass

from .cobs import CobsError, StreamDecoder, frame as cobs_frame
from .frame import Frame, FrameError, decode_frame, encode_frame
from .secure_session import SecurePacketDecoder, SecurePacketError, encrypt_packet


@dataclass(frozen=True, slots=True)
class DecodeProblem:
    stage: str
    reason: str
    decoded_packet: bytes | None = None


@dataclass(frozen=True, slots=True)
class DecodedPacket:
    frame: Frame
    inner_frame_bytes: bytes
    outer_packet_bytes: bytes


@dataclass(slots=True)
class SecureOutbound:
    session_key: bytes
    host_iv4: bytes
    counter: int = 0

    def wrap(self, inner: bytes) -> bytes:
        packet = encrypt_packet(inner, self.session_key, self.host_iv4, self.counter)
        self.counter = (self.counter + 1) & 0xFFFFFFFF
        return packet


class GfdiWireCodec:
    """Incremental wire codec with explicit decode-problem reporting.

    A malformed decoded packet is returned as ``DecodeProblem`` rather than
    silently discarded. COBS stream errors are also surfaced, while the stream
    decoder resets its local packet state according to its own bounded parser.
    """

    def __init__(
        self,
        *,
        max_encoded_packet: int = 16384,
        outbound_secure: SecureOutbound | None = None,
        inbound_secure: SecurePacketDecoder | None = None,
    ) -> None:
        self.stream = StreamDecoder(max_encoded_packet)
        self.outbound_secure = outbound_secure
        self.inbound_secure = inbound_secure

    def encode(self, frame: Frame) -> bytes:
        inner = encode_frame(frame)
        outer = self.outbound_secure.wrap(inner) if self.outbound_secure else inner
        return cobs_frame(outer)

    def feed(self, chunk: bytes) -> list[DecodedPacket | DecodeProblem]:
        try:
            packets = self.stream.feed(chunk)
        except CobsError as exc:
            return [DecodeProblem("cobs", str(exc), None)]

        out: list[DecodedPacket | DecodeProblem] = []
        for outer in packets:
            inner = outer
            if self.inbound_secure is not None:
                try:
                    inner = self.inbound_secure.decrypt(outer)
                except SecurePacketError as exc:
                    out.append(DecodeProblem("secure", str(exc), outer))
                    continue
            try:
                parsed = decode_frame(inner)
            except FrameError as exc:
                out.append(DecodeProblem("frame", str(exc), inner))
                continue
            out.append(DecodedPacket(parsed, inner, outer))
        return out
