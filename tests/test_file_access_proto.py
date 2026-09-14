from __future__ import annotations

from uuid import UUID

import pytest

import garmin_proto_lab.file_access_proto as fa

from garmin_proto_lab.file_access_proto import (
    CancelTransferRequest,
    CancelTransferStatus,
    ChecksumMethod,
    DataTypeFormat,
    FileAccessCapabilities,
    FileDataType,
    FileItemReference,
    GetItemChecksumRequest,
    GetItemChecksumResponse,
    GetItemChecksumResult,
    ItemAccessResult,
    ItemListRequest,
    ItemListStatus,
    MlrPipeConfigure,
    MlrPipeConfigureResponse,
    MlrPipeConfigureStatus,
    MlrPipeGeneralStatus,
    PullItemRequest,
    TransferFailureReason,
    TransferStatusRequest,
    TransferStatusResponse,
    MlrPipeDirection,
    TransferDirection,
    TransportProtocol,
    build_cancel_transfer_smart,
    build_file_access_smart,
    build_get_item_checksum_smart,
    build_item_list_smart,
    build_pull_item_smart,
    build_service_message,
    build_transfer_status_smart_response,
    encode_uuid,
    parse_cancel_transfer_smart_response,
    parse_get_item_checksum_smart_response,
    parse_item_list_smart_response,
    parse_pull_item_smart_response,
    parse_transfer_status_smart_request,
    parse_uuid,
    truncated_md5,
)
from garmin_proto_lab.protobuf_wire import (
    decode_sint32,
    encode_bool,
    encode_fixed64,
    encode_message,
    encode_sint32,
    encode_string,
    encode_uint,
    last_bytes,
    last_fixed64,
    last_varint,
    parse_fields,
)


def test_fixed64_sint32_and_uuid_vectors() -> None:
    assert encode_fixed64(1, 0x0011223344556677) == bytes.fromhex("097766554433221100")
    assert encode_sint32(5, -123) == bytes.fromhex("28f501")
    assert decode_sint32(245) == -123

    value = UUID("00112233-4455-6677-8899-aabbccddeeff")
    raw = encode_uuid(value)
    assert raw == bytes.fromhex("09776655443322110011ffeeddccbbaa9988")
    assert parse_uuid(raw) == value
    fields = parse_fields(raw)
    assert last_fixed64(fields, 1) == 0x0011223344556677
    assert last_fixed64(fields, 2) == 0x8899AABBCCDDEEFF


def test_capabilities_and_data_type_roundtrip() -> None:
    caps = FileAccessCapabilities(True, False, ChecksumMethod.TRUNCATED_MD5, 4_000_000, True)
    parsed_caps = FileAccessCapabilities.parse(caps.encode())
    assert parsed_caps.server_push_file_bundle_support is True
    assert parsed_caps.software_update_part_number_request_support is False
    assert parsed_caps.server_file_checksum_method is ChecksumMethod.TRUNCATED_MD5
    assert parsed_caps.checksum_max_file_size_byte == 4_000_000
    assert parsed_caps.custom_flag_support is True

    table: dict[int, str] = {}
    direct = FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "FIT_TYPE_4", 42)
    assert FileDataType.parse(direct.encode()).resolved_name(table) == "FIT_TYPE_4"
    keyed = FileDataType(DataTypeFormat.GDXML_DATA_TYPE, gdxml_string_key=42)
    assert FileDataType.parse(keyed.encode()).resolved_name(table) == "FIT_TYPE_4"
    assert FileDataType(DataTypeFormat.GDXML_FULL_FILE).resolved_name() == "GDXml"
    assert FileDataType(DataTypeFormat.DATA_XFER).resolved_name() == "AD_HOC_XFER"


def test_item_reference_and_item_list_request() -> None:
    uid = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    flag = UUID("11111111-2222-3333-4444-555555555555")
    item = FileItemReference(
        uid,
        FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "activity"),
        12345,
        requested_flags=5,
        transaction_id=99,
        modified_time=123,
    )
    assert FileItemReference.parse(item.encode()).uid == uid
    assert FileItemReference.parse(item.encode()).data_size == 12345

    req = ItemListRequest(
        transaction_id=77,
        max_item_count=25,
        exclude_all_flags=(flag,),
        get_flags=(flag,),
        included_data_types=(FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "activity"),),
        requested_modified_time=True,
    )
    smart = build_item_list_smart(req)
    outer = parse_fields(smart)
    service = last_bytes(outer, 43)
    assert service is not None
    body = last_bytes(parse_fields(service), 9)
    assert body is not None
    fields = parse_fields(body)
    assert last_varint(fields, 2) == 77
    assert last_varint(fields, 3) == 25
    assert len([field for field in fields if field.number == 4]) == 1
    assert len([field for field in fields if field.number == 5]) == 1
    assert last_varint(fields, 7) == 0


def test_item_list_and_pull_response_parsers() -> None:
    uid = UUID("12345678-1234-5678-9abc-def012345678")
    item = FileItemReference(uid, FileDataType(DataTypeFormat.GDXML_DATA_TYPE, "activity"), 321)
    listing_payload = (
        encode_uint(1, ItemListStatus.SUCCESS)
        + encode_uint(2, 7)
        + encode_uint(3, 88)
        + encode_message(4, item.encode())
    )
    listing_smart = build_file_access_smart(build_service_message(10, listing_payload))
    listing = parse_item_list_smart_response(listing_smart)
    assert listing.status is ItemListStatus.SUCCESS
    assert listing.session_id == 7
    assert listing.next_transaction_id == 88
    assert listing.items[0].uid == uid

    pull_payload = (
        encode_uint(1, ItemAccessResult.SUCCESS)
        + encode_uint(2, TransportProtocol.MULTILINK_TRANSPORT_PIPE)
        + encode_uint(3, 123)
        + encode_string(4, "/GARMIN/ACTIVITY.fit")
        + encode_uint(7, 15)
    )
    pull_smart = build_file_access_smart(build_service_message(2, pull_payload))
    pull = parse_pull_item_smart_response(pull_smart)
    assert pull.result is ItemAccessResult.SUCCESS
    assert pull.transport is TransportProtocol.MULTILINK_TRANSPORT_PIPE
    assert pull.transfer_handle == 123
    assert pull.compression_window == 15

    failure_payload = encode_uint(1, ItemAccessResult.DATA_TYPE_ERROR) + encode_uint(8, 9) + encode_sint32(9, -12)
    failure = parse_pull_item_smart_response(build_file_access_smart(build_service_message(2, failure_payload)))
    assert failure.data_specific_error_source == 9
    assert failure.data_specific_error_code == -12


def test_pull_request_and_mlr_configure_exact_vector() -> None:
    uid = UUID("00112233-4455-6677-8899-aabbccddeeff")
    item = FileItemReference(uid, None, 100)
    request = PullItemRequest(item, 20, (TransportProtocol.MULTILINK_TRANSPORT_PIPE,), 10, 15)
    smart = build_pull_item_smart(request)
    service = last_bytes(parse_fields(smart), 43)
    body = last_bytes(parse_fields(service or b""), 1)
    assert body is not None
    fields = parse_fields(body)
    assert last_varint(fields, 2) == 20
    assert last_varint(fields, 3) == 0
    assert last_varint(fields, 4) == 10
    assert last_varint(fields, 5) == 15

    configure = MlrPipeConfigure(0x0102030405060708, MlrPipeDirection.READ)
    assert configure.encode() == bytes.fromhex("00000807060504030201")
    ok = MlrPipeConfigureResponse.parse(b"\x00\x00\x00")
    assert ok.successful
    assert ok.general_status is MlrPipeGeneralStatus.SUCCESS
    assert ok.configure_status is MlrPipeConfigureStatus.SUCCESS
    assert MlrPipeConfigureResponse.parse(b"\x00\x01").general_status is MlrPipeGeneralStatus.UNKNOWN_COMMAND
    assert MlrPipeConfigureResponse.parse(b"\x00\x02").general_status is MlrPipeGeneralStatus.INVALID_COMMAND_DATA
    assert MlrPipeConfigureResponse.parse(b"\x00\x00\x01").configure_status is MlrPipeConfigureStatus.UNKNOWN_TRANSFER_HANDLE
    assert MlrPipeConfigureResponse.parse(b"\x00\x00\x02").configure_status is MlrPipeConfigureStatus.UNEXPECTED_TRANSFER_DIRECTION
    assert MlrPipeConfigureResponse.parse(b"\x00\x00\x03").configure_status is MlrPipeConfigureStatus.UNEXPECTED_TRANSFER_SIZE
    with pytest.raises(ValueError, match="missing configure status"):
        MlrPipeConfigureResponse.parse(b"\x00\x00")


def test_transfer_status_request_and_response_wire() -> None:
    uid = UUID("00112233-4455-6677-8899-aabbccddeeff")
    request = TransferStatusRequest(
        uid=uid,
        transfer_handle=123,
        failure_reason=TransferFailureReason.TRANSPORT_FAILED,
        transport_status=7,
        transport_provider_id=8,
        transport_error_code=9,
        new_priority=30,
    )
    smart = build_file_access_smart(build_service_message(5, request.encode()))
    parsed = parse_transfer_status_smart_request(smart)
    assert parsed is not None
    assert parsed.uid == uid
    assert parsed.transfer_handle == 123
    assert parsed.failure_reason is TransferFailureReason.TRANSPORT_FAILED
    assert parsed.transport_status == 7
    assert parsed.transport_provider_id == 8
    assert parsed.transport_error_code == 9
    assert parsed.new_priority == 30

    response = build_transfer_status_smart_response(TransferStatusResponse(22))
    service = last_bytes(parse_fields(response), 43)
    body = last_bytes(parse_fields(service or b""), 21)
    assert body is not None
    assert last_varint(parse_fields(body), 1) == 22
    empty = build_transfer_status_smart_response()
    empty_service = last_bytes(parse_fields(empty), 43)
    assert last_bytes(parse_fields(empty_service or b""), 21) == b""


def test_cancel_transfer_request_response_wire() -> None:
    smart = build_cancel_transfer_smart(CancelTransferRequest(0x12345678))
    service = last_bytes(parse_fields(smart), 43)
    body = last_bytes(parse_fields(service or b""), 18)
    assert body is not None
    assert last_varint(parse_fields(body), 1) == 0x12345678

    success = build_file_access_smart(build_service_message(19, encode_uint(1, 0)))
    assert parse_cancel_transfer_smart_response(success).status is CancelTransferStatus.SUCCESS
    unknown = build_file_access_smart(build_service_message(19, encode_uint(1, 1)))
    assert parse_cancel_transfer_smart_response(unknown).status is CancelTransferStatus.UNKNOWN_TRANSFER
    with pytest.raises(ValueError, match="uint32"):
        CancelTransferRequest(1 << 32).encode()


def test_get_item_checksum_wire_and_truncation_algorithm() -> None:
    uid = UUID("00112233-4455-6677-8899-aabbccddeeff")
    request = build_get_item_checksum_smart(GetItemChecksumRequest(uid))
    service = last_bytes(parse_fields(request), 43)
    body = last_bytes(parse_fields(service or b""), 25)
    assert body is not None
    assert parse_uuid(last_bytes(parse_fields(body), 1) or b"") == uid

    data = b"garmin-file-access-checksum-vector"
    expected = int.from_bytes(__import__("hashlib").md5(data).digest()[:8], "little")
    assert truncated_md5(data) == expected
    response_body = (
        encode_message(1, encode_uuid(uid))
        + encode_uint(2, GetItemChecksumResult.SUCCESS)
        + encode_fixed64(3, expected)
    )
    response = parse_get_item_checksum_smart_response(
        build_file_access_smart(build_service_message(26, response_body))
    )
    assert response.uid == uid
    assert response.result is GetItemChecksumResult.SUCCESS
    assert response.checksum_truncated_md5 == expected


def test_remaining_file_access_service_schema_vectors() -> None:
    uid = UUID("12345678-1234-5678-9abc-def012345678")
    flag1 = UUID(int=1)
    flag2 = UUID(int=2)
    item = fa.FileItemReference(uid, fa.FileDataType(fa.DataTypeFormat.GDXML_DATA_TYPE, "FIT_TYPE_4"), 99)

    push = fa.build_push_item_smart(fa.PushItemRequest(item, 30, requested_compression_window=15))
    service = last_bytes(parse_fields(push), 43)
    body = last_bytes(parse_fields(service or b""), 3)
    fields = parse_fields(body or b"")
    assert last_varint(fields, 2) == 30
    assert last_varint(fields, 3) == 0
    assert last_varint(fields, 4) == 15
    push_response = (
        encode_uint(1, fa.ItemAccessResult.SUCCESS)
        + encode_uint(2, 12)
        + encode_uint(3, fa.TransportProtocol.MULTILINK_TRANSPORT_PIPE)
        + encode_uint(4, 44)
        + encode_string(5, "/GARMIN/NEW.FIT")
        + encode_uint(8, 15)
    )
    parsed_push = fa.parse_push_item_smart_response(
        fa.build_file_access_smart(fa.build_service_message(4, push_response))
    )
    assert parsed_push.offset == 12
    assert parsed_push.transfer_handle == 44

    delete = fa.build_delete_item_smart(fa.DeleteItemRequest(uid, fa.TransferDirection.PULL))
    service = last_bytes(parse_fields(delete), 43)
    body = last_bytes(parse_fields(service or b""), 13)
    fields = parse_fields(body or b"")
    assert fa.parse_uuid(last_bytes(fields, 1) or b"") == uid
    assert last_varint(fields, 2) == fa.TransferDirection.PULL
    delete_response = fa.parse_delete_item_smart_response(
        fa.build_file_access_smart(fa.build_service_message(14, encode_uint(1, fa.DeleteItemResult.ITEM_DOES_NOT_EXIST)))
    )
    assert delete_response.result is fa.DeleteItemResult.ITEM_DOES_NOT_EXIST

    priority = fa.build_priority_update_smart(fa.PriorityUpdateRequest(44, 40))
    service = last_bytes(parse_fields(priority), 43)
    body = last_bytes(parse_fields(service or b""), 6)
    assert [(f.number, f.value) for f in parse_fields(body or b"")] == [(1, 44), (2, 40)]
    priority_response = fa.parse_priority_update_smart_response(
        fa.build_file_access_smart(fa.build_service_message(7, encode_uint(1, fa.PriorityUpdateStatus.SUCCESS)))
    )
    assert priority_response.status is fa.PriorityUpdateStatus.SUCCESS

    cancel_note = fa.parse_item_list_cancel_smart_notification(
        fa.build_file_access_smart(fa.build_service_message(11, encode_uint(1, 55)))
    )
    assert cancel_note is not None and cancel_note.session_id == 55

    added = fa.parse_item_added_smart_notification(
        fa.build_file_access_smart(fa.build_service_message(12, encode_message(1, item.encode())))
    )
    assert added is not None and added.items[0].uid == uid

    modify = fa.build_modify_flags_smart(fa.ModifyFlagsRequest(uid, (flag1,), (flag2,)))
    service = last_bytes(parse_fields(modify), 43)
    body = last_bytes(parse_fields(service or b""), 15)
    fields = parse_fields(body or b"")
    assert fa.parse_uuid(last_bytes(fields, 1) or b"") == uid
    assert len([f for f in fields if f.number == 2]) == 1
    assert len([f for f in fields if f.number == 3]) == 1
    modify_response = fa.parse_modify_flags_smart_response(
        fa.build_file_access_smart(fa.build_service_message(16, encode_uint(1, fa.ModifyFlagsStatus.SUCCESS)))
    )
    assert modify_response.status is fa.ModifyFlagsStatus.SUCCESS

    updated_payload = (
        encode_message(1, fa.encode_uuid(uid))
        + encode_message(2, fa.encode_uuid(flag1))
        + encode_message(3, fa.encode_uuid(flag2))
    )
    updated = fa.parse_item_updated_smart_notification(
        fa.build_file_access_smart(fa.build_service_message(17, updated_payload))
    )
    assert updated is not None
    assert updated.uid == uid
    assert updated.changed_flags_set == (flag1,)
    assert updated.changed_flags_clear == (flag2,)

    resource = fa.parse_resource_update_smart_notification(
        fa.build_file_access_smart(
            fa.build_service_message(
                20,
                encode_uint(1, fa.ResourceUpdateStatus.CURRENT_PRIORITY_LOWERED) + encode_uint(2, 19),
            )
        )
    )
    assert resource is not None
    assert resource.status is fa.ResourceUpdateStatus.CURRENT_PRIORITY_LOWERED
    assert resource.current_priority == 19

    sync_button = fa.parse_sync_button_smart_notification(
        fa.build_file_access_smart(fa.build_service_message(22, b""))
    )
    assert sync_button is not None and sync_button.raw_fields == ()

    part_request = fa.build_software_update_part_number_smart(
        fa.SoftwareUpdatePartNumberRequest(fa.SoftwareUpdateRequestor.APP)
    )
    service = last_bytes(parse_fields(part_request), 43)
    body = last_bytes(parse_fields(service or b""), 23)
    assert last_varint(parse_fields(body or b""), 1) == fa.SoftwareUpdateRequestor.APP
    parts = fa.parse_software_update_part_number_smart_response(
        fa.build_file_access_smart(
            fa.build_service_message(24, encode_string(1, "006-B1234-00") + encode_string(1, "006-B5678-00"))
        )
    )
    assert parts.part_numbers == ("006-B1234-00", "006-B5678-00")
