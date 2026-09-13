from __future__ import annotations

import pytest

from garmin_proto_lab.configuration import flags_from_bitset
from garmin_proto_lab.handshake import (
    HandshakeError,
    HandshakePhase,
    HandshakeState,
    HostIdentity,
)


def _device_info(protocol: int = 150) -> bytes:
    out = bytearray()
    out += protocol.to_bytes(2, "little")
    out += (1234).to_bytes(2, "little")
    out += (0x01020304).to_bytes(4, "little")
    out += (900).to_bytes(2, "little")
    out += (247).to_bytes(2, "little")
    for text in (b"Watch", b"Owner Watch", b"Model"):
        out += bytes([len(text)]) + text
    return bytes(out)


def test_host_device_info_response_transaction_protocol() -> None:
    identity = HostIdentity(11548, "Independent Client", "Open", "Lab")
    raw = identity.build_device_info_ack_payload(150)
    assert int.from_bytes(raw[0:2], "little") == 150
    assert int.from_bytes(raw[2:4], "little") == 0xFFFF
    assert int.from_bytes(raw[4:8], "little") == 0xFFFFFFFF
    assert int.from_bytes(raw[8:10], "little") == 11548
    assert int.from_bytes(raw[10:12], "little") == 0xFFFF
    pos = 12
    for expected in ("Independent Client", "Open", "Lab"):
        length = raw[pos]
        pos += 1
        assert raw[pos : pos + length].decode() == expected
        pos += length
    assert raw[pos:] == b"\x01"


def test_host_device_info_response_legacy_protocol_fallback() -> None:
    identity = HostIdentity(1, "Host", "Maker", "Model")
    assert int.from_bytes(identity.build_device_info_ack_payload(149)[:2], "little") == 113
    identity_no_tx = HostIdentity(1, "Host", "Maker", "Model", use_transaction_ids=False)
    assert int.from_bytes(identity_no_tx.build_device_info_ack_payload(200)[:2], "little") == 113


def test_modern_handshake_state_device_info_configuration_complete() -> None:
    identity = HostIdentity(1, "Host", "Maker", "Model")
    handshake = HandshakeState(identity, frozenset({3, 6, 71}))
    step = handshake.receive_device_info(_device_info())
    assert step.device.product_number == 1234
    assert handshake.phase is HandshakePhase.WAITING_PEER_CONFIGURATION

    # Peer advertises bit 3 but not 4/90; effective compatibility set adds 4.
    config = handshake.receive_configuration(bytes([1, 1 << 3]))
    assert config.peer_configuration.flags == frozenset({3})
    assert config.effective_peer_flags == frozenset({3, 4})
    assert config.acknowledgement_payload == b""
    assert flags_from_bitset(config.host_configuration_payload[1:]) == frozenset({3, 6, 71})
    assert handshake.phase is HandshakePhase.WAITING_HOST_CONFIGURATION_ACK

    handshake.host_configuration_acknowledged()
    assert handshake.complete
    assert handshake.events == [
        "device_info_received",
        "peer_configuration_received",
        "handshake_complete",
    ]


def test_handshake_wrong_order_and_malformed_input_fail_closed() -> None:
    handshake = HandshakeState(HostIdentity(1, "H", "M", "X"), frozenset())
    with pytest.raises(HandshakeError, match="invalid"):
        handshake.receive_configuration(b"\x00")
    with pytest.raises(HandshakeError, match="Device Information"):
        handshake.receive_device_info(b"short")
    assert handshake.phase is HandshakePhase.FAILED
