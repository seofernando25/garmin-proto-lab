"""Parser for legacy GFDI Battery Status message 5023."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class BatteryState(IntEnum):
    GOOD = 0x10
    OK = 0x20
    LOW = 0x30
    CRITICAL = 0x40
    NEW = 0x50
    INVALID = 0x70


@dataclass(frozen=True, slots=True)
class BatteryStatus:
    state_bits: int
    capacity_percent: int

    @property
    def state(self) -> BatteryState | None:
        try:
            return BatteryState(self.state_bits)
        except ValueError:
            return None


def parse_battery_status(payload: bytes) -> BatteryStatus:
    if len(payload) < 6:
        raise ValueError("battery status payload shorter than six bytes")
    return BatteryStatus(payload[0] & 0x70, payload[2])


def build_phone_battery_status(capacity_percent: int) -> bytes:
    """Build the six-byte host battery update observed in the legacy handler."""
    if not 0 <= capacity_percent <= 100:
        raise ValueError("capacity_percent must be 0..100")
    return bytes([0xFF, 0x00, capacity_percent, 0xFF, 0xFF, 0xFF])
