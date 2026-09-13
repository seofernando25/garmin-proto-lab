from __future__ import annotations

import pytest
from hypothesis import given, strategies as st

from garmin_proto_lab.ancs import (
    ActionFlag,
    ActionID,
    AncsError,
    AppAttributeID,
    AttributeValue,
    CategoryID,
    EventFlag,
    EventID,
    FeatureFlag,
    GetAppAttributesRequest,
    GetAppAttributesResponse,
    GetNotificationAttributesRequest,
    GetNotificationAttributesResponse,
    NotificationAction,
    NotificationActions,
    NotificationAttributeID,
    NotificationAttributeRequest,
    NotificationSource,
    PerformAndroidAction,
    PerformNotificationAction,
    parse_control_point,
    validate_control_point,
)


def test_notification_source_nine_byte_layout() -> None:
    source = NotificationSource(
        EventID.ADDED,
        EventFlag.IMPORTANT | EventFlag.POSITIVE_ACTION,
        CategoryID.EMAIL,
        200,
        0x12345678,
        FeatureFlag.HAS_ANDROID_ACTIONS,
    )
    raw = source.encode()
    assert raw == bytes.fromhex("000a067f7856341202")
    parsed = NotificationSource.parse(raw)
    assert parsed.notification_id == 0x12345678
    assert parsed.category_count == 127
    assert parsed.event_flags & EventFlag.POSITIVE_ACTION


def test_get_notification_attributes_request_layout_roundtrip() -> None:
    request = GetNotificationAttributesRequest(
        0x01020304,
        (
            NotificationAttributeRequest(NotificationAttributeID.APP_IDENTIFIER),
            NotificationAttributeRequest(NotificationAttributeID.TITLE, 64),
            NotificationAttributeRequest(NotificationAttributeID.MESSAGE, 256),
            NotificationAttributeRequest(NotificationAttributeID.ACTIONS, 20, 3, 1),
        ),
    )
    raw = request.encode()
    assert raw[:5] == bytes.fromhex("0004030201")
    assert raw[5:] == bytes.fromhex("000140000300017f140301")
    assert GetNotificationAttributesRequest.parse(raw) == request


def test_notification_attribute_response_roundtrip_and_utf8_safe_truncation() -> None:
    response = GetNotificationAttributesResponse(
        7,
        (
            AttributeValue(NotificationAttributeID.TITLE, "café 🔔".encode()),
            AttributeValue(NotificationAttributeID.MESSAGE_SIZE, b"7"),
        ),
    )
    raw = response.encode({int(NotificationAttributeID.TITLE): 6})
    parsed = GetNotificationAttributesResponse.parse(raw)
    assert parsed.notification_id == 7
    assert parsed.attributes[0].text() == "café "
    assert parsed.attributes[1].value == b"7"


def test_app_attribute_request_response_roundtrip() -> None:
    request = GetAppAttributesRequest("com.example.mail", (AppAttributeID.DISPLAY_NAME,))
    assert GetAppAttributesRequest.parse(request.encode()) == request

    response = GetAppAttributesResponse(
        "com.example.mail",
        (AttributeValue(AppAttributeID.DISPLAY_NAME, b"Example Mail"),),
    )
    assert GetAppAttributesResponse.parse(response.encode()) == response


def test_notification_actions_and_control_actions() -> None:
    actions = NotificationActions(
        (
            NotificationAction(4, ActionFlag.POSITIVE_ACTION, "Reply", 20),
            NotificationAction(9, ActionFlag.DISMISS_ACTION, "Dismiss", 20),
        )
    )
    raw = actions.encode()
    parsed = NotificationActions.parse(raw)
    assert [a.title for a in parsed.actions] == ["Reply", "Dismiss"]
    assert parsed.actions[1].flags & ActionFlag.DISMISS_ACTION

    simple = PerformNotificationAction(0x01020304, ActionID.NEGATIVE)
    assert PerformNotificationAction.parse(simple.encode()) == simple
    android = PerformAndroidAction(0x01020304, 4, "ok")
    assert PerformAndroidAction.parse(android.encode()) == android


def test_malformed_ancs_inputs_fail_closed() -> None:
    with pytest.raises(AncsError, match="nine bytes"):
        NotificationSource.parse(b"short")
    with pytest.raises(AncsError, match="truncated"):
        GetNotificationAttributesRequest.parse(bytes.fromhex("000100000001"))
    with pytest.raises(AncsError, match="unterminated"):
        GetAppAttributesRequest.parse(b"\x01abc")
    with pytest.raises(AncsError, match="expected 2"):
        NotificationActions.parse(bytes([2, 1, 0, 1, ord("x")]))


@given(
    st.integers(min_value=0, max_value=0xFFFFFFFF),
    st.integers(min_value=0, max_value=127),
    st.integers(min_value=0, max_value=0x1F),
    st.integers(min_value=0, max_value=0x07),
)
def test_notification_source_property_roundtrip(
    notification_id: int,
    category_count: int,
    event_flags: int,
    feature_flags: int,
) -> None:
    source = NotificationSource(
        EventID.MODIFIED,
        EventFlag(event_flags),
        CategoryID.OTHER,
        category_count,
        notification_id,
        FeatureFlag(feature_flags),
    )
    assert NotificationSource.parse(source.encode()) == source


def test_control_point_parser_and_semantic_error_codes() -> None:
    action = PerformNotificationAction(123, ActionID.POSITIVE).encode()
    assert parse_control_point(action) == PerformNotificationAction(123, ActionID.POSITIVE)
    assert validate_control_point(action) == 0
    assert validate_control_point(b"") == 160
    assert validate_control_point(b"D") == 160
    # command + notification id, but no requested attributes
    assert validate_control_point(bytes([0, 1, 0, 0, 0])) == 162
