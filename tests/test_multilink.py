from __future__ import annotations

from uuid import UUID

import pytest

from garmin_proto_lab.multilink import (
    DEFAULT_INDEPENDENT_CLIENT_ID,
    FILE_TRANSFER_PIPE_SERVICE_IDS,
    MULTILINK_PRIMARY_CHARACTERISTICS,
    MULTILINK_PAIRED_CHARACTERISTICS,
    CloseAllRequest,
    CloseAllResponse,
    CloseHandleRequest,
    CloseHandleResponse,
    CloseStatus,
    MultiLinkError,
    RegisterServiceRequest,
    RegisterServiceResponse,
    RegisterStatus,
    candidate_characteristic_pairs,
    paired_characteristic,
    parse_control_message,
)


def test_registration_request_and_success_response() -> None:
    connection = 0x0102030405060708
    request = RegisterServiceRequest(connection, 0x2018, True)
    assert request.encode() == bytes.fromhex("00000807060504030201182002")

    response_raw = bytes.fromhex("00010807060504030201182000800107")
    response = RegisterServiceResponse.parse(response_raw)
    assert response.connection_id == connection
    assert response.service_id == 0x2018
    assert response.status is RegisterStatus.SUCCESS
    assert response.handle == 0x80
    assert response.reliable is True
    assert response.revision == 7
    assert parse_control_message(response_raw) == response


def test_registration_already_in_use_returns_alternate_characteristic() -> None:
    raw = bytes.fromhex("000108070605040302010400031128")
    response = RegisterServiceResponse.parse(raw)
    assert response.status is RegisterStatus.ALREADY_IN_USE
    assert response.alternate_characteristic == UUID("6a4e2811-667b-11e3-949a-0800200c9a66")


def test_close_control_vectors() -> None:
    connection = 0x0102030405060708
    assert CloseAllRequest(connection).encode() == bytes.fromhex("000508070605040302010000")
    close_all = CloseAllResponse.parse(bytes.fromhex("00060807060504030201000000"))
    assert close_all.connection_id == connection
    assert close_all.status is CloseStatus.SUCCESS

    assert CloseHandleRequest(connection, 0x2018, 0x80).encode() == bytes.fromhex(
        "00020807060504030201182080"
    )
    response = CloseHandleResponse.parse(bytes.fromhex("0003080706050403020118208000"))
    assert response.handle == 0x80
    assert response.status is CloseStatus.SUCCESS


def test_characteristic_pair_mapping_and_file_transfer_pool() -> None:
    assert paired_characteristic(MULTILINK_PRIMARY_CHARACTERISTICS[0]) == MULTILINK_PAIRED_CHARACTERISTICS[0]
    both = frozenset({MULTILINK_PRIMARY_CHARACTERISTICS[0], MULTILINK_PAIRED_CHARACTERISTICS[0]})
    assert candidate_characteristic_pairs(both) == (
        (MULTILINK_PRIMARY_CHARACTERISTICS[0], MULTILINK_PAIRED_CHARACTERISTICS[0]),
    )
    only_primary = frozenset({MULTILINK_PRIMARY_CHARACTERISTICS[1]})
    assert candidate_characteristic_pairs(only_primary) == (
        (MULTILINK_PRIMARY_CHARACTERISTICS[1], MULTILINK_PRIMARY_CHARACTERISTICS[1]),
    )
    assert FILE_TRANSFER_PIPE_SERVICE_IDS == (0x2018, 0x4018, 0x6018, 0x8018, 0xA018, 0xC018, 0xE018)


def test_multilink_parser_rejects_short_or_non_control_messages() -> None:
    with pytest.raises(MultiLinkError):
        parse_control_message(b"\x80\x00")
    with pytest.raises(MultiLinkError):
        RegisterServiceResponse.parse(b"\x00\x01")


def test_default_client_id_matches_bundled_garmin_client_uuid() -> None:
    assert DEFAULT_INDEPENDENT_CLIENT_ID == 0x01
    assert DEFAULT_INDEPENDENT_CLIENT_ID.to_bytes(8, "little") == b"\x01" + b"\x00" * 7
