from garmin_proto_lab.capabilities import (
    CONNECT_MOBILE_FIT_LINK,
    CURRENT_TIME_REQUEST_SUPPORT,
    DEVICE_INITIATES_SYNC,
    EXPLICIT_ARCHIVE,
    FITNESS_HOST_FLAGS,
    GNCS,
    HOST_INITIATED_SYNC_REQUESTS,
    PAIRING_HOST_FLAGS,
    REQUEST_PAIR_FLOW,
    SYNC,
    SYNC_2_FILE_ACCESS,
    workflow_host_flags,
)


def test_recovered_pairing_and_fitness_host_flags() -> None:
    assert PAIRING_HOST_FLAGS == frozenset({0, 3, 4, 5, 64, 71})
    assert FITNESS_HOST_FLAGS == frozenset({0, 3, 4, 5, 29, 64, 71, 90})
    assert workflow_host_flags(notifications=True, file_access=True) == frozenset({0, 3, 4, 5, 6, 64, 71, 90})
    assert CONNECT_MOBILE_FIT_LINK == 0
    assert SYNC == 3
    assert DEVICE_INITIATES_SYNC == 4
    assert HOST_INITIATED_SYNC_REQUESTS == 5
    assert GNCS == 6
    assert EXPLICIT_ARCHIVE == 29
    assert REQUEST_PAIR_FLOW == 64
    assert CURRENT_TIME_REQUEST_SUPPORT == 71
    assert SYNC_2_FILE_ACCESS == 90
