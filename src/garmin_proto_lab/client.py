"""High-level semantic client over the independent GFDI message link.

The client intentionally stays small: it automates only message behaviors that
have static protocol evidence and leaves target-dependent file indexes,
notification policy, and pairing UI decisions to the application.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from .ancs import (
    CategoryID,
    EventFlag,
    EventID,
    FeatureFlag,
    GetAppAttributesRequest,
    GetNotificationAttributesRequest,
    NotificationRecord,
    NotificationSource,
    parse_control_point,
    validate_control_point,
)
from .auth_protocol import AUTH_IDS, AuthProtocolEngine, EstablishedSession
from .battery import BatteryStatus, build_phone_battery_status, parse_battery_status
from .configuration import Configuration
from .device_settings import TIME_REQUEST_CONFIGURATION_FLAG, build_legacy_time_settings, build_time_updated_event
from .file_access_client import FileAccessControlClient
from .file_access_proto import FILE_ACCESS_CONFIGURATION_FLAG, FileAccessCapabilities
from .file_client import DirectoryListing, FileReadResult, FileTransferClient, ListedFile
from .filetransfer import DirectoryFilter
from .fit import FitInspection, inspect_fit, is_activity_or_health_subtype
from .frame import Frame, ResponseStatus
from .gncs import (
    AncsSemanticError,
    ControlPointResponse,
    ControlPointResponseType,
    DataSourceResponse,
    DataSourceStatus,
    GNCS_CONTROL_POINT_MESSAGE_ID,
    GNCS_DATA_SOURCE_MESSAGE_ID,
    GNCS_SUBSCRIPTION_MESSAGE_ID,
    SubscriptionIntent,
    SubscriptionRequest,
    SubscriptionResponse,
    SubscriptionStatus,
    build_data_source_chunk,
    decode_control_point_payload,
    encode_notification_source_payload,
)
from .handshake import ConfigurationStep, HandshakeState, HostIdentity
from .link import GfdiMessageLink
from .protobuf_link import ProtobufSmartLink
from .smart_proto import (
    FeatureCapabilitiesRequest,
    FeatureCapabilitiesResponse,
    GncsCapabilitiesAdvertisement,
    NotificationDisabledReason,
    build_feature_capabilities_request,
    is_connection_ready_notification,
    parse_feature_capabilities_response,
    semantic_version_to_int,
)
from .sync import FileReady, QueuedDownload, SyncRequest
from .time_sync import CurrentTimeResponse, build_response
from .transport import DiscoveredDevice, TransportState

MESSAGE_FILE_READY = 5009
MESSAGE_BATTERY = 5023
MESSAGE_DEVICE_INFO = 5024
MESSAGE_SET_DEVICE_SETTINGS = 5026
MESSAGE_QUEUED_DOWNLOAD = 5027
MESSAGE_SYSTEM_EVENT = 5030
MESSAGE_GNCS_NOTIFICATION_SOURCE = 5033
MESSAGE_GNCS_CONTROL_POINT = GNCS_CONTROL_POINT_MESSAGE_ID
MESSAGE_SYNC_REQUEST = 5037
MESSAGE_CONFIGURATION = 5050
MESSAGE_CURRENT_TIME = 5052
FEATURE_CAPABILITIES_CONFIGURATION_FLAG = 95
GNCS_CONFIGURATION_FLAG = 6


class ClientError(RuntimeError):
    pass


class SemanticKind(str, Enum):
    AUTH_ESTABLISHED = "auth_established"
    DEVICE_INFO = "device_info"
    CONFIGURATION = "configuration"
    BATTERY = "battery"
    TIME_REQUEST = "time_request"
    TIME_SYNC_COMPLETE = "time_sync_complete"
    QUEUED_DOWNLOAD = "queued_download"
    SYNC_REQUEST = "sync_request"
    FILE_READY = "file_ready"
    DIRECTORY = "directory"
    FILE_READ = "file_read"
    NOTIFICATION_SUBSCRIPTION = "notification_subscription"
    GNCS_CONTROL_POINT = "gncs_control_point"
    FEATURE_CAPABILITIES = "feature_capabilities"
    CONNECTION_READY = "connection_ready"
    UNKNOWN = "unknown"
    PROBLEM = "problem"


@dataclass(frozen=True, slots=True)
class SemanticEvent:
    kind: SemanticKind
    value: Any
    message_type: int

    def accessible_text(self) -> str:
        """Plain-language event label suitable for speech/log UIs."""
        labels = {
            SemanticKind.AUTH_ESTABLISHED: "Secure watch session established",
            SemanticKind.DEVICE_INFO: "Watch identity received",
            SemanticKind.CONFIGURATION: "Watch capabilities received",
            SemanticKind.BATTERY: "Watch battery status received",
            SemanticKind.TIME_REQUEST: "Watch requested current time",
            SemanticKind.TIME_SYNC_COMPLETE: "Watch time synchronization complete",
            SemanticKind.QUEUED_DOWNLOAD: "Watch announced queued downloads",
            SemanticKind.SYNC_REQUEST: "Watch requested synchronization",
            SemanticKind.FILE_READY: "Watch announced a file ready for transfer",
            SemanticKind.DIRECTORY: "Watch file directory received",
            SemanticKind.FILE_READ: "Watch file transfer complete",
            SemanticKind.NOTIFICATION_SUBSCRIPTION: "Watch changed notification subscription",
            SemanticKind.GNCS_CONTROL_POINT: "Watch requested notification details or action",
            SemanticKind.FEATURE_CAPABILITIES: "Watch feature capabilities received",
            SemanticKind.CONNECTION_READY: "Watch reported protocol connection ready",
            SemanticKind.UNKNOWN: "Unknown watch message received",
            SemanticKind.PROBLEM: "Watch protocol problem",
        }
        return labels[self.kind]


@dataclass(frozen=True, slots=True)
class ActivityHealthTransfer:
    remote: ListedFile
    file: FileReadResult
    fit: FitInspection


@dataclass(frozen=True, slots=True)
class NotificationPolicy:
    enabled: bool = True
    phone_number_feature: bool = False

    def respond(self, request: SubscriptionRequest) -> SubscriptionResponse:
        intent = int(request.intent)
        if intent not in (int(SubscriptionIntent.UNSUBSCRIBE), int(SubscriptionIntent.SUBSCRIBE)):
            return SubscriptionResponse(SubscriptionStatus.NOT_SUPPORTED, intent, 0)
        if intent == int(SubscriptionIntent.SUBSCRIBE) and not self.enabled:
            return SubscriptionResponse(SubscriptionStatus.NOT_READY, intent, 0)
        flags = request.feature_flags
        if not self.phone_number_feature:
            flags &= ~1
        return SubscriptionResponse(SubscriptionStatus.SUCCESSFUL, intent, flags)


Clock = Callable[[], datetime]


def _local_now() -> datetime:
    return datetime.now().astimezone()


@dataclass(slots=True)
class GarminClient:
    link: GfdiMessageLink
    identity: HostIdentity
    host_configuration_flags: frozenset[int]
    notification_policy: NotificationPolicy = field(default_factory=NotificationPolicy)
    clock: Clock = _local_now
    auth: AuthProtocolEngine | None = None
    file_transfer: FileTransferClient | None = None
    protobuf: ProtobufSmartLink | None = None
    file_access_control: FileAccessControlClient | None = None
    handshake: HandshakeState = field(init=False)
    events: asyncio.Queue[SemanticEvent] = field(default_factory=asyncio.Queue)
    session_key: bytes | None = None
    feature_capabilities: FeatureCapabilitiesResponse | None = None
    file_access_capabilities: FileAccessCapabilities | None = None
    notifications: dict[int, NotificationRecord] = field(default_factory=dict)
    _time_request_seen: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: set[asyncio.Task[Any]] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.handshake = HandshakeState(self.identity, self.host_configuration_flags)
        if self.file_transfer is None:
            self.file_transfer = FileTransferClient(self.link)
        elif self.file_transfer.link is not self.link:
            raise ClientError("file-transfer client and semantic client must share the same GFDI link")
        if self.protobuf is None:
            self.protobuf = ProtobufSmartLink(self.link)
        elif self.protobuf.link is not self.link:
            raise ClientError("protobuf client and semantic client must share the same GFDI link")
        if self.file_access_control is None:
            self.file_access_control = FileAccessControlClient(self.protobuf)
        elif self.file_access_control.protobuf is not self.protobuf:
            raise ClientError("FileAccess control client and semantic client must share the same protobuf link")
        previous_protobuf_handler = self.protobuf.request_handler

        async def protobuf_request(request_id: int, serialized_smart: bytes):
            if is_connection_ready_notification(serialized_smart):
                await self._emit(SemanticKind.CONNECTION_READY, {"request_id": request_id}, 5043)
                return True
            if self.file_access_control is not None:
                outcome = await self.file_access_control.handle_incoming(request_id, serialized_smart)
                if outcome is not False and outcome is not None:
                    return outcome
            if previous_protobuf_handler is not None:
                result = previous_protobuf_handler(request_id, serialized_smart)
                if hasattr(result, "__await__"):
                    return await result
                return result
            return False

        self.protobuf.request_handler = protobuf_request
        if self.auth is not None:
            if self.auth.link is not self.link:
                raise ClientError("authentication engine and semantic client must share the same GFDI link")
            previous = self.auth.on_established

            async def established(session: EstablishedSession) -> None:
                self.session_key = session.session_key
                await self._emit(SemanticKind.AUTH_ESTABLISHED, session, 5111)
                if previous is not None:
                    result = previous(session)
                    if hasattr(result, "__await__"):
                        await result

            self.auth.on_established = established
        self.link.set_incoming_callback(self._on_incoming)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain_background(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=False)

    async def _emit(self, kind: SemanticKind, value: Any, message_type: int) -> None:
        await self.events.put(SemanticEvent(kind, value, message_type))

    async def _on_incoming(self, dispatched: Any) -> None:
        frame: Frame = dispatched.frame
        message_type = frame.message_type
        if self.protobuf is not None and await self.protobuf.handle(frame):
            return
        if self.auth is not None and message_type in AUTH_IDS:
            if await self.auth.handle(frame):
                return
        if self.file_transfer is not None and await self.file_transfer.handle(frame):
            return
        try:
            if message_type == MESSAGE_DEVICE_INFO:
                step = self.handshake.receive_device_info(frame.payload)
                if self.auth is not None:
                    self.auth.dual_pairing = step.device.dual_pairing
                if self.protobuf is not None and step.device.max_packet_size > 14:
                    self.protobuf.set_payload_limit(step.device.max_packet_size)
                await self.link.respond(frame, ResponseStatus.ACK, step.acknowledgement_payload)
                await self._emit(SemanticKind.DEVICE_INFO, step.device, message_type)
                return

            if message_type == MESSAGE_CONFIGURATION:
                step = self.handshake.receive_configuration(frame.payload)
                await self.link.respond(frame, ResponseStatus.ACK, step.acknowledgement_payload)
                await self._emit(SemanticKind.CONFIGURATION, step.peer_configuration, message_type)
                self._spawn(self._finish_configuration(step))
                return

            if message_type == MESSAGE_BATTERY:
                battery = parse_battery_status(frame.payload)
                await self.link.respond(frame, ResponseStatus.ACK, b"")
                await self._emit(SemanticKind.BATTERY, battery, message_type)
                return

            if message_type == MESSAGE_CURRENT_TIME:
                response = build_response(frame.payload, self.clock())
                await self.link.respond(frame, ResponseStatus.ACK, response.to_payload())
                self._time_request_seen.set()
                await self._emit(SemanticKind.TIME_REQUEST, response, message_type)
                return

            if message_type == MESSAGE_QUEUED_DOWNLOAD:
                queued = QueuedDownload.parse(frame.payload)
                await self.link.respond(frame, ResponseStatus.ACK, b"\x00")
                await self._emit(SemanticKind.QUEUED_DOWNLOAD, queued, message_type)
                return

            if message_type == MESSAGE_SYNC_REQUEST:
                request = SyncRequest.parse(frame.payload)
                await self.link.respond(frame, ResponseStatus.ACK, b"")
                await self._emit(SemanticKind.SYNC_REQUEST, request, message_type)
                return

            if message_type == MESSAGE_FILE_READY:
                file_ready = FileReady.parse(frame.payload)
                await self.link.respond(frame, ResponseStatus.ACK, b"")
                await self._emit(SemanticKind.FILE_READY, file_ready, message_type)
                return

            if message_type == GNCS_SUBSCRIPTION_MESSAGE_ID:
                request = SubscriptionRequest.parse(frame.payload)
                response = self.notification_policy.respond(request)
                await self.link.respond(frame, ResponseStatus.ACK, response.encode())
                await self._emit(SemanticKind.NOTIFICATION_SUBSCRIPTION, request, message_type)
                return

            if message_type == MESSAGE_GNCS_CONTROL_POINT:
                clear = decode_control_point_payload(frame.payload, self.session_key)
                ancs_error = validate_control_point(clear)
                response_type = (
                    ControlPointResponseType.SUCCESSFUL
                    if ancs_error == int(AncsSemanticError.NO_ERROR)
                    else ControlPointResponseType.ANCS_ERROR_OCCURRED
                )
                response = ControlPointResponse(response_type, ancs_error)
                await self.link.respond(frame, ResponseStatus.ACK, response.encode())
                value = parse_control_point(clear) if ancs_error == 0 else clear
                await self._emit(SemanticKind.GNCS_CONTROL_POINT, value, message_type)
                if ancs_error == 0 and isinstance(value, (GetNotificationAttributesRequest, GetAppAttributesRequest)):
                    self._spawn(self._serve_notification_attributes(value))
                return

            await self._emit(SemanticKind.UNKNOWN, frame, message_type)
        except Exception as exc:
            # Malformed semantic payloads receive LENGTH_ERROR and are surfaced.
            # If responding itself fails, preserve that error in the event queue.
            try:
                if not frame.is_response:
                    await self.link.respond(frame, ResponseStatus.LENGTH_ERROR, b"")
            finally:
                await self._emit(SemanticKind.PROBLEM, exc, message_type)

    async def _finish_configuration(self, step: ConfigurationStep) -> None:
        await self.link.request(MESSAGE_CONFIGURATION, step.host_configuration_payload)
        self.handshake.host_configuration_acknowledged()

    @property
    def peer_configuration(self) -> Configuration | None:
        return self.handshake.peer_configuration

    async def sync_time(self, *, request_timeout: float = 5.0) -> None:
        peer = self.peer_configuration
        if peer is None:
            raise ClientError("cannot synchronize time before peer configuration is known")
        now = self.clock()
        if TIME_REQUEST_CONFIGURATION_FLAG in peer.effective_flags():
            self._time_request_seen.clear()
            await self.link.request(MESSAGE_SYSTEM_EVENT, build_time_updated_event())
            try:
                await asyncio.wait_for(self._time_request_seen.wait(), request_timeout)
            except asyncio.TimeoutError as exc:
                raise ClientError("timed out waiting for device Current Time 5052 request") from exc
        else:
            await self.link.request(MESSAGE_SET_DEVICE_SETTINGS, build_legacy_time_settings(now))
        await self._emit(SemanticKind.TIME_SYNC_COMPLETE, now, MESSAGE_CURRENT_TIME)

    async def refresh_feature_capabilities(
        self,
        *,
        gncs_version: str = "0.1.0",
        notification_disabled_reason: NotificationDisabledReason | int | None = None,
        default_messaging_app_id: str = "",
        default_dialer_app_id: str = "",
    ) -> FeatureCapabilitiesResponse:
        """Run the statically recovered Core/GNCS protobuf capability exchange.

        The call is intentionally explicit and only allowed when the peer's
        legacy Configuration advertises flag 95.  The independent client uses
        its own semantic version instead of impersonating Garmin's library
        version.
        """
        peer = self.peer_configuration
        if peer is None:
            raise ClientError("cannot query feature capabilities before peer configuration")
        if FEATURE_CAPABILITIES_CONFIGURATION_FLAG not in peer.effective_flags():
            raise ClientError("watch did not advertise protobuf feature-capabilities flag 95")
        if self.protobuf is None:
            raise ClientError("protobuf transport is not configured")
        gncs = None
        if GNCS_CONFIGURATION_FLAG in self.host_configuration_flags:
            gncs = GncsCapabilitiesAdvertisement(
                semantic_version_to_int(gncs_version),
                notification_disabled_reason,
                default_messaging_app_id,
                default_dialer_app_id,
            )
        file_access = None
        if FILE_ACCESS_CONFIGURATION_FLAG in self.host_configuration_flags:
            # The recovered FileAccess manager contributes an empty capabilities
            # message; this advertises protocol participation without inventing
            # optional server features.
            file_access = FileAccessCapabilities().encode()
        request = FeatureCapabilitiesRequest(gncs=gncs, file_access=file_access)
        response_bytes = await self.protobuf.request(build_feature_capabilities_request(request))
        response = parse_feature_capabilities_response(response_bytes)
        self.feature_capabilities = response
        self.file_access_capabilities = (
            FileAccessCapabilities.parse(response.file_access)
            if response.file_access is not None
            else None
        )
        await self._emit(SemanticKind.FEATURE_CAPABILITIES, response, 5044)
        return response

    async def send_phone_battery(self, capacity_percent: int) -> None:
        await self.link.request(MESSAGE_BATTERY, build_phone_battery_status(capacity_percent))

    async def send_notification_source(self, source: NotificationSource) -> None:
        payload = encode_notification_source_payload(source.encode(), self.session_key)
        await self.link.request(MESSAGE_GNCS_NOTIFICATION_SOURCE, payload)

    async def publish_notification(
        self,
        record: NotificationRecord,
        *,
        category: CategoryID | int = CategoryID.OTHER,
        category_count: int = 1,
        silent: bool = False,
    ) -> None:
        """Register application-owned notification data and announce it to the watch."""
        self.notifications[record.notification_id] = record
        event_flags = EventFlag.SILENT if silent else EventFlag.IMPORTANT
        if record.positive_action_label:
            event_flags |= EventFlag.POSITIVE_ACTION
        if record.negative_action_label:
            event_flags |= EventFlag.NEGATIVE_ACTION
        feature_flags = FeatureFlag(0)
        if record.phone_number:
            feature_flags |= FeatureFlag.PHONE_NUMBER_AVAILABLE
        if record.actions:
            feature_flags |= FeatureFlag.HAS_ANDROID_ACTIONS
        if record.media_object_count:
            feature_flags |= FeatureFlag.HAS_MEDIA
        await self.send_notification_source(
            NotificationSource(
                EventID.ADDED,
                event_flags,
                category,
                category_count,
                record.notification_id,
                feature_flags,
            )
        )

    async def remove_notification(self, notification_id: int) -> None:
        self.notifications.pop(notification_id, None)
        await self.send_notification_source(
            NotificationSource(
                EventID.REMOVED,
                EventFlag.SILENT,
                CategoryID.OTHER,
                0,
                notification_id,
                FeatureFlag(0),
            )
        )

    async def _serve_notification_attributes(
        self,
        request: GetNotificationAttributesRequest | GetAppAttributesRequest,
    ) -> None:
        if isinstance(request, GetNotificationAttributesRequest):
            record = self.notifications.get(request.notification_id)
            if record is None:
                # Static Garmin behavior sends a removed source when the watch
                # asks about a notification that is no longer active.
                await self.send_notification_source(
                    NotificationSource(
                        EventID.REMOVED,
                        EventFlag.SILENT,
                        CategoryID.OTHER,
                        0,
                        request.notification_id,
                        FeatureFlag(0),
                    )
                )
                return
            response = record.attribute_response(request)
            await self.send_notification_data(response.encode())
            return

        # App attributes are served only from an application record we actually
        # own; do not invent a package display name.
        record = next(
            (item for item in self.notifications.values() if item.app_identifier == request.app_identifier),
            None,
        )
        if record is not None:
            await self.send_notification_data(record.app_attribute_response(request).encode())

    async def send_notification_data(
        self,
        payload: bytes,
        *,
        max_retries: int = 3,
        gfdi_payload_limit: int | None = None,
    ) -> None:
        """Send a GNCS/ANCS Data Source body using 5035 chunk acknowledgements."""
        if max_retries < 0:
            raise ClientError("max_retries cannot be negative")
        if gfdi_payload_limit is None:
            if self.handshake.device is None:
                raise ClientError("GFDI payload limit is unknown before Device Information")
            gfdi_payload_limit = self.handshake.device.max_packet_size

        offset = 0
        crc_seed = 0
        failures = 0
        while offset < len(payload):
            built = build_data_source_chunk(
                payload,
                data_offset=offset,
                crc_seed=crc_seed,
                gfdi_payload_limit=gfdi_payload_limit,
                session_key=self.session_key,
            )
            ack = await self.link.request(GNCS_DATA_SOURCE_MESSAGE_ID, built.payload)
            response = DataSourceResponse.parse(ack.payload)
            status = response.status
            if status is DataSourceStatus.TRANSFER_SUCCESSFUL:
                offset = built.next_data_offset
                crc_seed = built.next_crc_seed
                failures = 0
                continue
            if status in (DataSourceStatus.RESEND_LAST_DATA_PACKET, DataSourceStatus.CRC_MISMATCH):
                failures += 1
                if failures > max_retries:
                    raise ClientError(f"GNCS 5035 retry budget exceeded with status {int(status)}")
                continue
            if status is DataSourceStatus.DATA_OFFSET_MISMATCH:
                # Static sender advances using the current post-chunk offset/CRC
                # on this response. Preserve that behavior but bound repeats.
                failures += 1
                if failures > max_retries:
                    raise ClientError("GNCS 5035 offset mismatch retry budget exceeded")
                offset = built.next_data_offset
                crc_seed = built.next_crc_seed
                continue
            if status is DataSourceStatus.ABORT_REQUEST:
                raise ClientError("remote device aborted GNCS data transfer")
            raise ClientError(f"unknown GNCS data-source status {int(status)}")


    async def discover(self, timeout: float = 8.0) -> list[DiscoveredDevice]:
        return await self.link.transport.discover(timeout)

    async def connect(self, device: DiscoveredDevice) -> None:
        await self.link.connect(device)

    async def disconnect(self) -> None:
        await self.link.disconnect()
        if self.auth is not None:
            self.auth.state.reset()
        self.session_key = None

    async def list_files(self, filter_value: DirectoryFilter = DirectoryFilter.DEFAULT) -> DirectoryListing:
        assert self.file_transfer is not None
        listing = await self.file_transfer.list_directory(filter_value)
        await self._emit(SemanticKind.DIRECTORY, listing, 0)
        return listing

    async def read_file(self, file_index: int, *, compression: bool = True) -> FileReadResult:
        assert self.file_transfer is not None
        result = await self.file_transfer.read_file(file_index, compression=compression)
        await self._emit(SemanticKind.FILE_READ, result, 5002)
        return result

    async def archive_file(self, file_index: int) -> None:
        assert self.file_transfer is not None
        await self.file_transfer.archive_file(file_index)

    async def list_activity_health_files(
        self,
        filter_value: DirectoryFilter = DirectoryFilter.PENDING_UPLOADS_ONLY,
    ) -> tuple[ListedFile, ...]:
        listing = await self.list_files(filter_value)
        return tuple(
            item
            for item in listing.readable_unarchived
            if is_activity_or_health_subtype(item.entry.data_type, item.entry.subtype)
        )

    async def download_activity_health(
        self,
        remote: ListedFile,
        *,
        compression: bool = True,
        archive_after_success: bool = False,
    ) -> ActivityHealthTransfer:
        if not is_activity_or_health_subtype(remote.entry.data_type, remote.entry.subtype):
            raise ClientError("selected remote file is not a statically recognized activity/health FIT subtype")
        file_result = await self.read_file(remote.entry.file_index, compression=compression)
        inspection = inspect_fit(file_result.data)
        if inspection.file_type_raw is None:
            raise ClientError("downloaded FIT file has no File ID type")
        if int(inspection.file_type_raw) != remote.entry.subtype:
            raise ClientError(
                f"directory/file FIT type mismatch: directory={remote.entry.subtype} file={inspection.file_type_raw}"
            )
        if not inspection.is_activity_or_health:
            raise ClientError("downloaded FIT File ID is not an activity/health type")
        transfer = ActivityHealthTransfer(remote, file_result, inspection)
        if archive_after_success:
            await self.archive_file(remote.entry.file_index)
        return transfer

    def status_snapshot(self) -> dict[str, object]:
        transport = getattr(self.link, "transport", None)
        state = getattr(transport, "state", None)
        return {
            "transport": state.value if isinstance(state, TransportState) else str(state) if state is not None else "unknown",
            "handshake": self.handshake.phase.value,
            "authentication": self.auth.state.phase.value if self.auth is not None else "not_configured",
            "secure_session": self.session_key is not None,
            "file_transfer_active": bool(self.file_transfer and self.file_transfer.active),
            "feature_capabilities": self.feature_capabilities is not None,
            "next_gen_file_access": self.file_access_capabilities is not None,
        }
