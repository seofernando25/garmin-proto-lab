"""Recovered host configuration capability bits used by direct watch workflows."""
from __future__ import annotations

CONNECT_MOBILE_FIT_LINK = 0
SYNC = 3
DEVICE_INITIATES_SYNC = 4
HOST_INITIATED_SYNC_REQUESTS = 5
GNCS = 6
EXPLICIT_ARCHIVE = 29
REQUEST_PAIR_FLOW = 64
CURRENT_TIME_REQUEST_SUPPORT = 71
# SupportedCapability ordinal 90 is SYNC_2. FileTransferManager also contributes
# the same legacy Configuration bit to gate the FileAccess protobuf manager.
SYNC_2_FILE_ACCESS = 90
# Peer-side gate used to decide whether Core FeatureCapabilities can be queried.
FEATURE_CAPABILITIES = 95

PAIRING_HOST_FLAGS = frozenset(
    {
        CONNECT_MOBILE_FIT_LINK,
        SYNC,
        DEVICE_INITIATES_SYNC,
        HOST_INITIATED_SYNC_REQUESTS,
        REQUEST_PAIR_FLOW,
        CURRENT_TIME_REQUEST_SUPPORT,
    }
)

FITNESS_HOST_FLAGS = PAIRING_HOST_FLAGS | frozenset(
    {
        EXPLICIT_ARCHIVE,
        SYNC_2_FILE_ACCESS,
    }
)


def workflow_host_flags(*, notifications: bool = False, file_access: bool = False) -> frozenset[int]:
    flags = set(PAIRING_HOST_FLAGS)
    if notifications:
        flags.add(GNCS)
    if file_access:
        flags.add(SYNC_2_FILE_ACCESS)
    return frozenset(flags)
