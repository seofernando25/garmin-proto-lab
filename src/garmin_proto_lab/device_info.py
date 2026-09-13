"""Parser for GFDI Device Information (message 5024) static grammar."""
from __future__ import annotations

from dataclasses import dataclass


class DeviceInfoError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DeviceInformation:
    protocol_version: int
    product_number: int
    unit_id: int
    software_version: int
    max_packet_size: int
    bluetooth_friendly_name: str
    device_name: str
    model_name: str
    dual_pairing: bool = False
    ble_mac: str | None = None
    classic_mac: str | None = None
    limitation: int = 0


def _lp_string(payload: bytes, pos: int) -> tuple[str, int]:
    if pos >= len(payload):
        raise DeviceInfoError("missing length-prefixed string")
    n = payload[pos]
    pos += 1
    end = pos + n
    if end > len(payload):
        raise DeviceInfoError("truncated length-prefixed string")
    return payload[pos:end].decode("utf-8", errors="replace"), end


def _format_reversed_mac(raw6: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw6[::-1])


def parse_device_information(payload: bytes, suppress_extended_for_products: set[int] | None = None) -> DeviceInformation:
    if len(payload) < 12:
        raise DeviceInfoError("device information fixed fields are truncated")
    pos = 0
    protocol_version = int.from_bytes(payload[pos:pos+2], "little"); pos += 2
    product_number = int.from_bytes(payload[pos:pos+2], "little"); pos += 2
    unit_id = int.from_bytes(payload[pos:pos+4], "little"); pos += 4
    software_version = int.from_bytes(payload[pos:pos+2], "little"); pos += 2
    max_packet_size = int.from_bytes(payload[pos:pos+2], "little"); pos += 2
    friendly, pos = _lp_string(payload, pos)
    device_name, pos = _lp_string(payload, pos)
    model_name, pos = _lp_string(payload, pos)

    dual = False
    ble_mac = classic_mac = None
    limitation = 0
    suppressed = {2787, 3192, 3307}
    if suppress_extended_for_products:
        suppressed |= suppress_extended_for_products
    if pos < len(payload) and product_number not in suppressed:
        dual = payload[pos] == 1; pos += 1
        if dual:
            if pos + 12 > len(payload):
                raise DeviceInfoError("dual-pairing MAC fields are truncated")
            ble_mac = _format_reversed_mac(payload[pos:pos+6]); pos += 6
            classic_mac = _format_reversed_mac(payload[pos:pos+6]); pos += 6
        if pos < len(payload):
            limitation = payload[pos]
    return DeviceInformation(protocol_version, product_number, unit_id, software_version,
                             max_packet_size, friendly, device_name, model_name,
                             dual, ble_mac, classic_mac, limitation)
