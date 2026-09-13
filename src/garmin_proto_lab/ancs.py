"""ANCS-compatible notification payloads carried by Garmin GNCS messages.

These are platform-neutral byte codecs for the Apple-ANCS-shaped message model
used by Garmin's Android smart-notification bridge. GFDI transport wrapping is
implemented separately in :mod:`gncs` and :mod:`link`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag


class AncsError(ValueError):
    pass


class EventID(IntEnum):
    ADDED = 0
    MODIFIED = 1
    REMOVED = 2


class EventFlag(IntFlag):
    SILENT = 0x01
    IMPORTANT = 0x02
    PRE_EXISTING = 0x04
    POSITIVE_ACTION = 0x08
    NEGATIVE_ACTION = 0x10


class CategoryID(IntEnum):
    OTHER = 0
    INCOMING_CALL = 1
    MISSED_CALL = 2
    VOICEMAIL = 3
    SOCIAL = 4
    SCHEDULE = 5
    EMAIL = 6
    NEWS = 7
    HEALTH_AND_FITNESS = 8
    BUSINESS_AND_FINANCE = 9
    LOCATION = 10
    ENTERTAINMENT = 11
    SMS = 12


class FeatureFlag(IntFlag):
    PHONE_NUMBER_AVAILABLE = 0x01
    HAS_ANDROID_ACTIONS = 0x02
    HAS_MEDIA = 0x04


class CommandID(IntEnum):
    GET_NOTIFICATION_ATTRIBUTES = 0
    GET_APP_ATTRIBUTES = 1
    PERFORM_NOTIFICATION_ACTION = 2
    PERFORM_ANDROID_ACTION = 0x80


class ActionID(IntEnum):
    POSITIVE = 0
    NEGATIVE = 1


class NotificationAttributeID(IntEnum):
    APP_IDENTIFIER = 0
    TITLE = 1
    SUBTITLE = 2
    MESSAGE = 3
    MESSAGE_SIZE = 4
    DATE = 5
    POSITIVE_ACTION_LABEL = 6
    NEGATIVE_ACTION_LABEL = 7
    PHONE_NUMBER = 126
    ACTIONS = 127
    MEDIA_OBJECT_COUNT = 128
    CONVERSATION_ID = 129


class AppAttributeID(IntEnum):
    DISPLAY_NAME = 0


class ActionFlag(IntFlag):
    REQUEST_INPUT = 0x01
    POSITIVE_ACTION = 0x02
    NEGATIVE_ACTION = 0x04
    DISMISS_ACTION = 0x08


STRING_LIMIT_ATTRIBUTES = {
    NotificationAttributeID.TITLE,
    NotificationAttributeID.SUBTITLE,
    NotificationAttributeID.MESSAGE,
    NotificationAttributeID.PHONE_NUMBER,
    NotificationAttributeID.CONVERSATION_ID,
}


@dataclass(frozen=True, slots=True)
class NotificationSource:
    event_id: EventID | int
    event_flags: EventFlag | int
    category_id: CategoryID | int
    category_count: int
    notification_id: int
    feature_flags: FeatureFlag | int = 0

    def encode(self) -> bytes:
        if not 0 <= int(self.event_id) <= 0xFF:
            raise AncsError("event_id out of byte range")
        if not 0 <= int(self.event_flags) <= 0xFF:
            raise AncsError("event_flags out of byte range")
        if not 0 <= int(self.category_id) <= 0xFF:
            raise AncsError("category_id out of byte range")
        if not 0 <= self.category_count:
            raise AncsError("category_count cannot be negative")
        if not 0 <= self.notification_id <= 0xFFFFFFFF:
            raise AncsError("notification_id out of uint32 range")
        if not 0 <= int(self.feature_flags) <= 0xFF:
            raise AncsError("feature_flags out of byte range")
        return bytes(
            [
                int(self.event_id),
                int(self.event_flags),
                int(self.category_id),
                min(self.category_count, 127),
            ]
        ) + self.notification_id.to_bytes(4, "little") + bytes([int(self.feature_flags)])

    @classmethod
    def parse(cls, payload: bytes) -> "NotificationSource":
        if len(payload) != 9:
            raise AncsError("notification-source payload must be exactly nine bytes")
        raw_event, raw_category = payload[0], payload[2]
        try:
            event: EventID | int = EventID(raw_event)
        except ValueError:
            event = raw_event
        try:
            category: CategoryID | int = CategoryID(raw_category)
        except ValueError:
            category = raw_category
        return cls(
            event,
            EventFlag(payload[1]),
            category,
            payload[3],
            int.from_bytes(payload[4:8], "little"),
            FeatureFlag(payload[8]),
        )


@dataclass(frozen=True, slots=True)
class NotificationAttributeRequest:
    attribute_id: NotificationAttributeID | int
    max_length: int | None = None
    max_actions: int | None = None
    action_flags: int | None = None

    def encode(self) -> bytes:
        attribute_id = int(self.attribute_id)
        if not 0 <= attribute_id <= 0xFF:
            raise AncsError("notification attribute id out of byte range")
        out = bytearray([attribute_id])
        try:
            known = NotificationAttributeID(attribute_id)
        except ValueError:
            known = None
        if known in STRING_LIMIT_ATTRIBUTES:
            if self.max_length is None or not 0 <= self.max_length <= 0xFFFF:
                raise AncsError("string attribute requires uint16 max_length")
            out += self.max_length.to_bytes(2, "little")
        elif known is NotificationAttributeID.ACTIONS:
            if self.max_length is None or not 0 <= self.max_length <= 0xFF:
                raise AncsError("actions attribute requires byte max_length")
            if self.max_actions is None or not 0 <= self.max_actions <= 0xFF:
                raise AncsError("actions attribute requires byte max_actions")
            if self.action_flags is None or not 0 <= self.action_flags <= 0xFF:
                raise AncsError("actions attribute requires byte action_flags")
            out += bytes([self.max_length, self.max_actions, self.action_flags])
        return bytes(out)


@dataclass(frozen=True, slots=True)
class GetNotificationAttributesRequest:
    notification_id: int
    attributes: tuple[NotificationAttributeRequest, ...]

    def encode(self) -> bytes:
        if not self.attributes:
            raise AncsError("at least one notification attribute is required")
        if not 0 <= self.notification_id <= 0xFFFFFFFF:
            raise AncsError("notification_id out of uint32 range")
        return (
            bytes([CommandID.GET_NOTIFICATION_ATTRIBUTES])
            + self.notification_id.to_bytes(4, "little")
            + b"".join(attribute.encode() for attribute in self.attributes)
        )

    @classmethod
    def parse(cls, payload: bytes) -> "GetNotificationAttributesRequest":
        if len(payload) < 5 or payload[0] != CommandID.GET_NOTIFICATION_ATTRIBUTES:
            raise AncsError("invalid get-notification-attributes request")
        notification_id = int.from_bytes(payload[1:5], "little")
        pos = 5
        attrs: list[NotificationAttributeRequest] = []
        while pos < len(payload):
            raw = payload[pos]
            pos += 1
            try:
                attr: NotificationAttributeID | int = NotificationAttributeID(raw)
            except ValueError:
                attr = raw
            max_length = max_actions = action_flags = None
            if attr in STRING_LIMIT_ATTRIBUTES:
                if pos + 2 > len(payload):
                    raise AncsError("truncated notification attribute max_length")
                max_length = int.from_bytes(payload[pos : pos + 2], "little")
                pos += 2
            elif attr is NotificationAttributeID.ACTIONS:
                if pos + 3 > len(payload):
                    raise AncsError("truncated actions attribute parameters")
                max_length, max_actions, action_flags = payload[pos : pos + 3]
                pos += 3
            attrs.append(NotificationAttributeRequest(attr, max_length, max_actions, action_flags))
        if not attrs:
            raise AncsError("no notification attributes requested")
        return cls(notification_id, tuple(attrs))


@dataclass(frozen=True, slots=True)
class AttributeValue:
    attribute_id: NotificationAttributeID | AppAttributeID | int
    value: bytes

    def text(self) -> str:
        return self.value.decode("utf-8", errors="replace")


def _truncate_utf8(data: bytes, limit: int | None) -> bytes:
    if limit is None or limit <= 0 or len(data) <= limit:
        return data
    cut = data[:limit]
    while cut:
        try:
            cut.decode("utf-8")
            return cut
        except UnicodeDecodeError as exc:
            cut = cut[: exc.start]
    return b""


@dataclass(frozen=True, slots=True)
class GetNotificationAttributesResponse:
    notification_id: int
    attributes: tuple[AttributeValue, ...]

    def encode(self, limits: dict[int, int] | None = None) -> bytes:
        if not self.attributes:
            raise AncsError("at least one notification attribute is required")
        out = bytearray([CommandID.GET_NOTIFICATION_ATTRIBUTES])
        out += self.notification_id.to_bytes(4, "little")
        for attribute in self.attributes:
            attr_id = int(attribute.attribute_id)
            value = attribute.value
            if limits and attr_id in limits and attr_id != NotificationAttributeID.ACTIONS:
                value = _truncate_utf8(value, limits[attr_id])
            if len(value) > 0xFFFF:
                raise AncsError("notification attribute value exceeds uint16 length")
            out += bytes([attr_id]) + len(value).to_bytes(2, "little") + value
        return bytes(out)

    @classmethod
    def parse(cls, payload: bytes) -> "GetNotificationAttributesResponse":
        if len(payload) < 8 or payload[0] != CommandID.GET_NOTIFICATION_ATTRIBUTES:
            raise AncsError("invalid get-notification-attributes response")
        notification_id = int.from_bytes(payload[1:5], "little")
        pos = 5
        attrs: list[AttributeValue] = []
        while pos < len(payload):
            if pos + 3 > len(payload):
                raise AncsError("truncated notification attribute response header")
            raw = payload[pos]
            length = int.from_bytes(payload[pos + 1 : pos + 3], "little")
            pos += 3
            end = pos + length
            if end > len(payload):
                raise AncsError("truncated notification attribute response value")
            try:
                attr: NotificationAttributeID | int = NotificationAttributeID(raw)
            except ValueError:
                attr = raw
            attrs.append(AttributeValue(attr, bytes(payload[pos:end])))
            pos = end
        if not attrs:
            raise AncsError("notification attribute response contains no attributes")
        return cls(notification_id, tuple(attrs))


@dataclass(frozen=True, slots=True)
class GetAppAttributesRequest:
    app_identifier: str
    attributes: tuple[AppAttributeID | int, ...]

    def encode(self) -> bytes:
        app = self.app_identifier.encode("utf-8")
        if not app or b"\x00" in app:
            raise AncsError("app_identifier must be non-empty and NUL-free")
        if not self.attributes:
            raise AncsError("at least one app attribute is required")
        return bytes([CommandID.GET_APP_ATTRIBUTES]) + app + b"\x00" + bytes(int(attr) for attr in self.attributes)

    @classmethod
    def parse(cls, payload: bytes) -> "GetAppAttributesRequest":
        if len(payload) < 4 or payload[0] != CommandID.GET_APP_ATTRIBUTES:
            raise AncsError("invalid get-app-attributes request")
        try:
            end = payload.index(0, 1)
        except ValueError as exc:
            raise AncsError("unterminated app identifier") from exc
        if end == 1:
            raise AncsError("empty app identifier")
        attrs: list[AppAttributeID | int] = []
        for raw in payload[end + 1 :]:
            try:
                attrs.append(AppAttributeID(raw))
            except ValueError:
                attrs.append(raw)
        if not attrs:
            raise AncsError("no app attributes requested")
        return cls(payload[1:end].decode("utf-8", errors="replace"), tuple(attrs))


@dataclass(frozen=True, slots=True)
class GetAppAttributesResponse:
    app_identifier: str
    attributes: tuple[AttributeValue, ...]

    def encode(self) -> bytes:
        app = self.app_identifier.encode("utf-8")
        if not app or b"\x00" in app:
            raise AncsError("app_identifier must be non-empty and NUL-free")
        out = bytearray([CommandID.GET_APP_ATTRIBUTES]) + app + b"\x00"
        for attribute in self.attributes:
            value = attribute.value
            if len(value) > 0xFFFF:
                raise AncsError("app attribute value exceeds uint16 length")
            out += bytes([int(attribute.attribute_id)]) + len(value).to_bytes(2, "little") + value
        return bytes(out)

    @classmethod
    def parse(cls, payload: bytes) -> "GetAppAttributesResponse":
        if len(payload) < 5 or payload[0] != CommandID.GET_APP_ATTRIBUTES:
            raise AncsError("invalid get-app-attributes response")
        try:
            end = payload.index(0, 1)
        except ValueError as exc:
            raise AncsError("unterminated app identifier") from exc
        app = payload[1:end].decode("utf-8", errors="replace")
        pos = end + 1
        attrs: list[AttributeValue] = []
        while pos < len(payload):
            if pos + 3 > len(payload):
                raise AncsError("truncated app attribute response header")
            raw = payload[pos]
            length = int.from_bytes(payload[pos + 1 : pos + 3], "little")
            pos += 3
            end_value = pos + length
            if end_value > len(payload):
                raise AncsError("truncated app attribute value")
            try:
                attr: AppAttributeID | int = AppAttributeID(raw)
            except ValueError:
                attr = raw
            attrs.append(AttributeValue(attr, bytes(payload[pos:end_value])))
            pos = end_value
        if not attrs:
            raise AncsError("app attribute response contains no attributes")
        return cls(app, tuple(attrs))


@dataclass(frozen=True, slots=True)
class PerformNotificationAction:
    notification_id: int
    action_id: ActionID | int

    def encode(self) -> bytes:
        return bytes([CommandID.PERFORM_NOTIFICATION_ACTION]) + self.notification_id.to_bytes(4, "little") + bytes([int(self.action_id)])

    @classmethod
    def parse(cls, payload: bytes) -> "PerformNotificationAction":
        if len(payload) != 6 or payload[0] != CommandID.PERFORM_NOTIFICATION_ACTION:
            raise AncsError("perform-notification-action must be six bytes")
        raw = payload[5]
        try:
            action: ActionID | int = ActionID(raw)
        except ValueError:
            action = raw
        return cls(int.from_bytes(payload[1:5], "little"), action)


@dataclass(frozen=True, slots=True)
class PerformAndroidAction:
    notification_id: int
    action_id: int
    text: str | None = None

    def encode(self) -> bytes:
        if not 0 <= self.action_id <= 0xFF:
            raise AncsError("Android action id out of byte range")
        out = bytearray([CommandID.PERFORM_ANDROID_ACTION])
        out += self.notification_id.to_bytes(4, "little") + bytes([self.action_id])
        if self.text is not None:
            text = self.text.encode("utf-8")
            if b"\x00" in text:
                raise AncsError("Android action text cannot contain NUL")
            out += text + b"\x00"
        return bytes(out)

    @classmethod
    def parse(cls, payload: bytes) -> "PerformAndroidAction":
        if len(payload) < 6 or payload[0] != CommandID.PERFORM_ANDROID_ACTION:
            raise AncsError("invalid perform-Android-action payload")
        text = None
        if len(payload) > 6:
            try:
                end = payload.index(0, 6)
            except ValueError as exc:
                raise AncsError("unterminated Android action text") from exc
            text = payload[6:end].decode("utf-8", errors="replace")
        return cls(int.from_bytes(payload[1:5], "little"), payload[5], text)


@dataclass(frozen=True, slots=True)
class NotificationAction:
    action_id: int
    flags: ActionFlag | int
    title: str
    max_title_length: int = 255

    def encode(self) -> bytes:
        if not 0 <= self.action_id <= 0xFF or not 0 <= int(self.flags) <= 0xFF:
            raise AncsError("notification action id/flags out of range")
        title = _truncate_utf8(self.title.encode("utf-8"), min(self.max_title_length, 255))
        return bytes([self.action_id, int(self.flags), len(title)]) + title

    @classmethod
    def parse_from(cls, payload: bytes, offset: int = 0) -> tuple["NotificationAction", int]:
        if offset + 3 > len(payload):
            raise AncsError("truncated notification action header")
        action_id, flags, length = payload[offset : offset + 3]
        end = offset + 3 + length
        if end > len(payload):
            raise AncsError("truncated notification action title")
        return cls(action_id, ActionFlag(flags), payload[offset + 3 : end].decode("utf-8", errors="replace"), length), end


@dataclass(frozen=True, slots=True)
class NotificationActions:
    actions: tuple[NotificationAction, ...]

    def encode(self, max_actions: int | None = None) -> bytes:
        actions = self.actions if max_actions is None else self.actions[:max_actions]
        if len(actions) > 0xFF:
            raise AncsError("too many notification actions")
        return bytes([len(actions)]) + b"".join(action.encode() for action in actions)

    @classmethod
    def parse(cls, payload: bytes) -> "NotificationActions":
        if not payload:
            raise AncsError("notification-actions payload is empty")
        expected = payload[0]
        pos = 1
        actions: list[NotificationAction] = []
        while pos < len(payload):
            action, pos = NotificationAction.parse_from(payload, pos)
            actions.append(action)
        if len(actions) != expected:
            raise AncsError(f"expected {expected} actions, got {len(actions)}")
        return cls(tuple(actions))


def parse_control_point(payload: bytes):
    """Parse one ANCS-shaped GNCS Control Point command.

    Unknown command IDs are rejected; callers that need the Garmin semantic
    error byte can use :func:`validate_control_point`.
    """
    if not payload:
        raise AncsError("control-point payload is empty")
    command = payload[0]
    if command == CommandID.GET_NOTIFICATION_ATTRIBUTES:
        return GetNotificationAttributesRequest.parse(payload)
    if command == CommandID.GET_APP_ATTRIBUTES:
        return GetAppAttributesRequest.parse(payload)
    if command == CommandID.PERFORM_NOTIFICATION_ACTION:
        return PerformNotificationAction.parse(payload)
    if command == CommandID.PERFORM_ANDROID_ACTION:
        return PerformAndroidAction.parse(payload)
    raise AncsError(f"unknown control-point command {command}")


def validate_control_point(payload: bytes) -> int:
    """Return the ANCS semantic error code recovered from the GNCS validator.

    0 = no error, 160 = unknown command, 161 = malformed/invalid command,
    162 = invalid semantic parameter. The distinction is intentionally narrow
    and only made where the static validator makes it observable.
    """
    if not payload:
        return 160
    command = payload[0]
    if command not in {
        int(CommandID.GET_NOTIFICATION_ATTRIBUTES),
        int(CommandID.GET_APP_ATTRIBUTES),
        int(CommandID.PERFORM_NOTIFICATION_ACTION),
        int(CommandID.PERFORM_ANDROID_ACTION),
    }:
        return 160
    try:
        parsed = parse_control_point(payload)
    except AncsError as exc:
        message = str(exc)
        if "no notification attributes" in message or "at least one" in message or "empty app identifier" in message or "no app attributes" in message:
            return 162
        return 161
    if isinstance(parsed, GetNotificationAttributesRequest) and not parsed.attributes:
        return 162
    if isinstance(parsed, GetAppAttributesRequest) and (not parsed.app_identifier or not parsed.attributes):
        return 162
    return 0
