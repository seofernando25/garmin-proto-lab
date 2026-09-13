"""Static GFDI handshake helpers for Device Information and Configuration.

This module covers the modern 5024 -> 5050 branch that can be established from
static evidence.  Older/model-specific FIT-capability fallback is kept outside
this state machine until target-watch traces establish whether it is required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .configuration import Configuration, encode_configuration, parse_configuration
from .device_info import DeviceInformation, parse_device_information


class HandshakeError(RuntimeError):
    pass


def _lp_utf8(value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > 255:
        raw = raw[:255]
        # Avoid leaving a truncated UTF-8 sequence in the advertised label.
        while raw:
            try:
                raw.decode("utf-8")
                break
            except UnicodeDecodeError as exc:
                raw = raw[: exc.start]
    return bytes([len(raw)]) + raw


@dataclass(frozen=True, slots=True)
class HostIdentity:
    app_version: int
    friendly_name: str
    manufacturer: str
    model: str
    use_transaction_ids: bool = True
    legacy_protocol_version: int = 113
    transaction_protocol_version: int = 150
    product_number: int = 0xFFFF
    unit_id: int = 0xFFFFFFFF
    software_product: int = 0xFFFF
    dual_pairing_capability: int = 1

    def build_device_info_ack_payload(self, peer_protocol_version: int) -> bytes:
        if not 0 <= self.app_version <= 0xFFFF:
            raise HandshakeError("app_version out of uint16 range")
        protocol = (
            self.transaction_protocol_version
            if self.use_transaction_ids and peer_protocol_version >= self.transaction_protocol_version
            else self.legacy_protocol_version
        )
        values = (protocol, self.product_number, self.app_version, self.software_product)
        if any(not 0 <= value <= 0xFFFF for value in values):
            raise HandshakeError("host uint16 identity field out of range")
        if not 0 <= self.unit_id <= 0xFFFFFFFF:
            raise HandshakeError("unit_id out of uint32 range")
        if not 0 <= self.dual_pairing_capability <= 0xFF:
            raise HandshakeError("dual_pairing_capability out of byte range")
        return (
            protocol.to_bytes(2, "little")
            + self.product_number.to_bytes(2, "little")
            + self.unit_id.to_bytes(4, "little")
            + self.app_version.to_bytes(2, "little")
            + self.software_product.to_bytes(2, "little")
            + _lp_utf8(self.friendly_name)
            + _lp_utf8(self.manufacturer)
            + _lp_utf8(self.model)
            + bytes([self.dual_pairing_capability])
        )


class HandshakePhase(str, Enum):
    WAITING_DEVICE_INFO = "waiting_device_info"
    WAITING_PEER_CONFIGURATION = "waiting_peer_configuration"
    WAITING_HOST_CONFIGURATION_ACK = "waiting_host_configuration_ack"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DeviceInfoStep:
    device: DeviceInformation
    acknowledgement_payload: bytes


@dataclass(frozen=True, slots=True)
class ConfigurationStep:
    peer_configuration: Configuration
    effective_peer_flags: frozenset[int]
    acknowledgement_payload: bytes
    host_configuration_payload: bytes


@dataclass(slots=True)
class HandshakeState:
    identity: HostIdentity
    host_configuration_flags: frozenset[int]
    phase: HandshakePhase = HandshakePhase.WAITING_DEVICE_INFO
    device: DeviceInformation | None = None
    peer_configuration: Configuration | None = None
    events: list[str] = field(default_factory=list)

    def receive_device_info(self, payload: bytes) -> DeviceInfoStep:
        if self.phase is not HandshakePhase.WAITING_DEVICE_INFO:
            raise HandshakeError(f"5024 is invalid in {self.phase.value}")
        try:
            device = parse_device_information(payload)
            ack = self.identity.build_device_info_ack_payload(device.protocol_version)
        except Exception as exc:
            self.phase = HandshakePhase.FAILED
            self.events.append("device_info_failed")
            raise HandshakeError("failed to process Device Information 5024") from exc
        self.device = device
        self.phase = HandshakePhase.WAITING_PEER_CONFIGURATION
        self.events.append("device_info_received")
        return DeviceInfoStep(device, ack)

    def receive_configuration(self, payload: bytes) -> ConfigurationStep:
        if self.phase is not HandshakePhase.WAITING_PEER_CONFIGURATION:
            raise HandshakeError(f"5050 is invalid in {self.phase.value}")
        try:
            peer = parse_configuration(payload, allow_trailing=False)
            host_payload = encode_configuration(self.host_configuration_flags)
        except Exception as exc:
            self.phase = HandshakePhase.FAILED
            self.events.append("configuration_failed")
            raise HandshakeError("failed to process Configuration 5050") from exc
        self.peer_configuration = peer
        self.phase = HandshakePhase.WAITING_HOST_CONFIGURATION_ACK
        self.events.append("peer_configuration_received")
        return ConfigurationStep(peer, peer.effective_flags(), b"", host_payload)

    def host_configuration_acknowledged(self) -> None:
        if self.phase is not HandshakePhase.WAITING_HOST_CONFIGURATION_ACK:
            raise HandshakeError(f"host Configuration ACK is invalid in {self.phase.value}")
        self.phase = HandshakePhase.COMPLETE
        self.events.append("handshake_complete")

    @property
    def complete(self) -> bool:
        return self.phase is HandshakePhase.COMPLETE
