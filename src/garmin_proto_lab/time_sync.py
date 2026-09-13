"""GFDI Current Time (5052) response helpers.

Protocol facts: P-0320 in ``spec/PROTOCOL.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

GARMIN_EPOCH_UNIX_SECONDS = 631065600  # 1989-12-31 00:00:00 UTC


@dataclass(frozen=True, slots=True)
class CurrentTimeResponse:
    echo: bytes
    current_time: int
    utc_offset_seconds: int
    next_dst_start: int
    next_dst_end: int

    def to_payload(self) -> bytes:
        if len(self.echo) != 4:
            raise ValueError("current-time request echo must be four bytes")
        fields = (self.current_time, self.utc_offset_seconds, self.next_dst_start, self.next_dst_end)
        return self.echo + b"".join((value & 0xFFFFFFFF).to_bytes(4, "little") for value in fields)


def garmin_seconds_from_unix(unix_seconds: int) -> int:
    return unix_seconds - GARMIN_EPOCH_UNIX_SECONDS


def _state_at(unix_second: int, zone: tzinfo) -> tuple[timedelta, timedelta]:
    dt = datetime.fromtimestamp(unix_second, tz=zone)
    return dt.utcoffset() or timedelta(0), dt.dst() or timedelta(0)


def _next_transition(after_unix: int, zone: tzinfo, search_days: int = 1100) -> int | None:
    """Find the next UTC-second where the zone's offset/DST state changes.

    Python's ``zoneinfo`` intentionally does not expose a transition iterator,
    so this performs a bounded daily search followed by a binary search to the
    first changed second.  The ~3-year default is enough to prove "no normal
    seasonal transition" for fixed-offset zones while staying deterministic.
    """
    initial = _state_at(after_unix, zone)
    lo = after_unix
    step = 24 * 60 * 60
    hi = lo + step
    limit = after_unix + search_days * step
    while hi <= limit and _state_at(hi, zone) == initial:
        lo = hi
        hi += step
    if hi > limit:
        return None
    # State at lo matches initial and state at hi differs. Locate first second
    # with the new state. Timezone transitions occur at whole seconds for all
    # modern zones relevant to an Android watch.
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _state_at(mid, zone) == initial:
            lo = mid
        else:
            hi = mid
    return hi


def next_dst_transitions(now: datetime) -> tuple[int, int]:
    """Return ``(next_start, next_end)`` as Garmin-epoch seconds.

    This mirrors the recovered Android logic: inspect the next timezone
    transition, classify it by whether the post-transition instant is in DST,
    then obtain the following transition starting one day later.  A fixed zone
    or a zone without a future transition in the bounded search returns zeros.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    zone = now.tzinfo
    now_unix = int(now.timestamp())
    first = _next_transition(now_unix, zone)
    if first is None:
        return 0, 0
    first_post = datetime.fromtimestamp(first, tz=zone)
    first_is_dst = bool(first_post.dst() and first_post.dst() != timedelta(0))
    second = _next_transition(first + 24 * 60 * 60, zone)
    if second is None:
        # The recovered code expects a pair.  Refuse to invent one if the zone
        # database does not provide the second transition.
        return 0, 0
    first_garmin = garmin_seconds_from_unix(first)
    second_garmin = garmin_seconds_from_unix(second)
    if first_is_dst:
        return first_garmin, second_garmin
    return second_garmin, first_garmin


def build_response(request_payload: bytes, now: datetime) -> CurrentTimeResponse:
    """Build the complete statically reconstructed 5052 response data."""
    if len(request_payload) < 4:
        raise ValueError("5052 request payload must contain at least four bytes")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    unix = int(now.timestamp())
    offset = int((now.utcoffset() or timedelta(0)).total_seconds())
    dst_start, dst_end = next_dst_transitions(now)
    return CurrentTimeResponse(
        request_payload[:4],
        garmin_seconds_from_unix(unix),
        offset,
        dst_start,
        dst_end,
    )


def build_basic_response(request_payload: bytes, now: datetime) -> CurrentTimeResponse:
    """Backward-compatible alias for :func:`build_response`."""
    return build_response(request_payload, now)
