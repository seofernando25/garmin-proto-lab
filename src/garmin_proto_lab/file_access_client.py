"""Next-generation GDI FileAccess client and MultiLink/MLR pull path.

The protobuf control plane, MultiLink registration, MLR ARQ behavior, transfer
status lifecycle, resume offsets, checksum verification and zlib pull mode are
implemented from the recovered protocol definitions.
"""
from __future__ import annotations

import asyncio
import inspect
import zlib
from dataclasses import dataclass
from uuid import UUID
from typing import Callable

from .file_access_proto import (
    CancelTransferRequest,
    CancelTransferStatus,
    DeleteItemRequest,
    DeleteItemResult,
    ModifyFlagsRequest,
    ModifyFlagsStatus,
    PriorityUpdateRequest,
    PriorityUpdateStatus,
    FileDataType,
    GetItemChecksumRequest,
    GetItemChecksumResult,
    FileItemReference,
    ItemAccessResult,
    ItemListRequest,
    ItemListStatus,
    MlrPipeConfigure,
    MlrPipeConfigureResponse,
    PullItemRequest,
    PullItemResponse,
    MlrPipeDirection,
    TransferPriorityLevel,
    TransferStatusRequest,
    TransferStatusResponse,
    TransportProtocol,
    build_cancel_transfer_smart,
    build_delete_item_smart,
    build_modify_flags_smart,
    build_priority_update_smart,
    build_get_item_checksum_smart,
    build_item_list_smart,
    build_pull_item_smart,
    build_transfer_status_smart_response,
    parse_cancel_transfer_smart_response,
    parse_delete_item_smart_response,
    parse_item_added_smart_notification,
    parse_item_list_cancel_smart_notification,
    parse_item_updated_smart_notification,
    parse_modify_flags_smart_response,
    parse_priority_update_smart_response,
    parse_resource_update_smart_notification,
    parse_sync_button_smart_notification,
    parse_get_item_checksum_smart_response,
    parse_item_list_smart_response,
    parse_pull_item_smart_response,
    parse_transfer_status_smart_request,
    truncated_md5,
)
from .fit import is_activity_or_health_data_type_name
from .mlr import ReliableMlrSession
from .multilink_client import MultiLinkClient, MultiLinkClientError, MultiLinkService
from .protobuf_link import ProtobufSmartLink
from .protobuf_wire import ProtobufWireError


class FileAccessControlError(RuntimeError):
    pass


class FileAccessTransferError(FileAccessControlError):
    pass


@dataclass(frozen=True, slots=True)
class FileAccessListing:
    items: tuple[FileItemReference, ...]
    next_transaction_id: int | None
    pages: int
    data_type_names: tuple[str | None, ...] = ()

    @property
    def activity_health_items(self) -> tuple[FileItemReference, ...]:
        return tuple(
            item
            for item, name in zip(self.items, self.data_type_names, strict=False)
            if is_activity_or_health_data_type_name(name)
        )


@dataclass(frozen=True, slots=True)
class PullNegotiation:
    item: FileItemReference
    response: PullItemResponse

    @property
    def transfer_handle(self) -> int:
        if self.response.transfer_handle is None:
            raise FileAccessControlError("successful pull response has no transfer handle")
        return self.response.transfer_handle

    @property
    def expected_size(self) -> int | None:
        return self.item.data_size


@dataclass(frozen=True, slots=True)
class IncomingTransferStatus:
    request_id: int
    request: TransferStatusRequest


@dataclass(frozen=True, slots=True)
class FileAccessChecksum:
    remote: int
    local: int

    @property
    def matches(self) -> bool:
        return self.remote == self.local


@dataclass(frozen=True, slots=True)
class FileAccessDownload:
    item: FileItemReference
    negotiation: PullNegotiation
    service: MultiLinkService
    data: bytes
    transfer_status: TransferStatusRequest
    start_offset: int = 0
    checksum: FileAccessChecksum | None = None


@dataclass(frozen=True, slots=True)
class FileAccessEvent:
    kind: str
    value: object
    request_id: int


class FileAccessControlClient:
    def __init__(self, protobuf: ProtobufSmartLink, *, max_pages: int = 256) -> None:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.protobuf = protobuf
        self.max_pages = max_pages
        self._tracked_handles: set[int] = set()
        self._status_waiters: dict[int, asyncio.Future[IncomingTransferStatus]] = {}
        self._pending_status: dict[int, IncomingTransferStatus] = {}
        self.events: asyncio.Queue[FileAccessEvent] = asyncio.Queue()

    async def list_items(
        self,
        *,
        transaction_id: int | None = None,
        exclude_all_flags: tuple[UUID, ...] = (),
        get_flags: tuple[UUID, ...] = (),
        included_data_types: tuple[FileDataType, ...] = (),
        requested_modified_time: bool = False,
        max_item_count: int | None = None,
    ) -> FileAccessListing:
        """Read all pages of an ItemList transaction."""
        request = ItemListRequest(
            transaction_id=transaction_id,
            max_item_count=max_item_count,
            exclude_all_flags=exclude_all_flags,
            get_flags=get_flags,
            included_data_types=included_data_types,
            requested_modified_time=requested_modified_time,
        )
        items: list[FileItemReference] = []
        data_type_names: list[str | None] = []
        string_table: dict[int, str] = {}
        pages = 0
        next_transaction: int | None = None
        while True:
            pages += 1
            if pages > self.max_pages:
                raise FileAccessControlError("FileAccess item-list pagination exceeded configured page bound")
            response = parse_item_list_smart_response(await self.protobuf.request(build_item_list_smart(request)))
            if response.status is not ItemListStatus.SUCCESS:
                raise FileAccessControlError(
                    f"FileAccess item-list failed with status {int(response.status) if response.status is not None else 'missing'}"
                )
            for item in response.items:
                items.append(item)
                data_type_names.append(
                    item.data_type.resolved_name(string_table) if item.data_type is not None else None
                )
            if response.session_id is None:
                next_transaction = response.next_transaction_id
                break
            request = ItemListRequest(session_id=response.session_id)
        return FileAccessListing(tuple(items), next_transaction, pages, tuple(data_type_names))

    async def begin_pull(
        self,
        item: FileItemReference,
        *,
        priority: int = int(TransferPriorityLevel.STANDARD_START),
        offset: int = 0,
        request_compression: bool = True,
    ) -> PullNegotiation:
        if item.uid is None:
            raise FileAccessControlError("pull requires an item UID")
        request = PullItemRequest(
            item,
            priority,
            (TransportProtocol.MULTILINK_TRANSPORT_PIPE,),
            offset,
            15 if request_compression else None,
        )
        response = parse_pull_item_smart_response(await self.protobuf.request(build_pull_item_smart(request)))
        if response.result is not ItemAccessResult.SUCCESS:
            details = []
            if response.file_path:
                details.append(f"path={response.file_path}")
            if response.error_source is not None:
                details.append(f"source={response.error_source}")
            if response.error_code is not None:
                details.append(f"code={response.error_code}")
            if response.data_specific_error_source is not None:
                details.append(f"data_source={response.data_specific_error_source}")
            if response.data_specific_error_code is not None:
                details.append(f"data_code={response.data_specific_error_code}")
            suffix = f" ({', '.join(details)})" if details else ""
            raise FileAccessControlError(
                f"FileAccess pull failed with result {int(response.result) if response.result is not None else 'missing'}{suffix}"
            )
        if response.transport is not TransportProtocol.MULTILINK_TRANSPORT_PIPE:
            raise FileAccessControlError("successful pull did not select MultiLink Transport Pipe")
        if response.transfer_handle is None:
            raise FileAccessControlError("successful pull response is missing transfer handle")
        if response.compression_window is not None:
            if not request_compression or not 9 <= response.compression_window <= 15:
                raise FileAccessControlError(f"invalid negotiated compression window {response.compression_window}")
        return PullNegotiation(item, response)

    async def update_priority(self, transfer_handle: int, new_priority: int) -> None:
        response = parse_priority_update_smart_response(
            await self.protobuf.request(
                build_priority_update_smart(PriorityUpdateRequest(transfer_handle, new_priority))
            )
        )
        if response.status is not PriorityUpdateStatus.SUCCESS:
            value = int(response.status) if response.status is not None else -1
            raise FileAccessControlError(f"FileAccess priority update failed with status {value}")

    async def delete_item(self, uid: UUID, direction) -> None:
        response = parse_delete_item_smart_response(
            await self.protobuf.request(build_delete_item_smart(DeleteItemRequest(uid, direction)))
        )
        if response.result not in (DeleteItemResult.SUCCESS, DeleteItemResult.ITEM_DOES_NOT_EXIST):
            value = int(response.result) if response.result is not None else -1
            raise FileAccessControlError(f"FileAccess delete failed with result {value}")

    async def modify_flags(
        self,
        uid: UUID,
        *,
        set_flags: tuple[UUID, ...] = (),
        clear_flags: tuple[UUID, ...] = (),
    ) -> None:
        response = parse_modify_flags_smart_response(
            await self.protobuf.request(
                build_modify_flags_smart(ModifyFlagsRequest(uid, set_flags, clear_flags))
            )
        )
        if response.status is not ModifyFlagsStatus.SUCCESS:
            value = int(response.status) if response.status is not None else -1
            raise FileAccessControlError(f"FileAccess modify-flags failed with status {value}")

    async def get_item_checksum(self, uid: UUID):
        response = parse_get_item_checksum_smart_response(
            await self.protobuf.request(build_get_item_checksum_smart(GetItemChecksumRequest(uid)))
        )
        if response.uid is not None and response.uid != uid:
            raise FileAccessControlError("FileAccess checksum response UID does not match request")
        return response

    async def verify_item_checksum(self, item: FileItemReference, data: bytes) -> FileAccessChecksum:
        if item.uid is None:
            raise FileAccessControlError("checksum verification requires item UID")
        response = await self.get_item_checksum(item.uid)
        if response.result is not GetItemChecksumResult.SUCCESS:
            value = int(response.result) if response.result is not None else -1
            raise FileAccessControlError(f"FileAccess checksum request failed with result {value}")
        if response.checksum_truncated_md5 is None:
            raise FileAccessControlError("successful FileAccess checksum response omitted checksum")
        result = FileAccessChecksum(response.checksum_truncated_md5, truncated_md5(data))
        if not result.matches:
            raise FileAccessTransferError(
                f"FileAccess checksum mismatch remote=0x{result.remote:016x} local=0x{result.local:016x}"
            )
        return result

    def track_transfer(self, transfer_handle: int) -> None:
        if not 0 <= transfer_handle <= 0xFFFFFFFF:
            raise FileAccessControlError("transfer handle must fit uint32")
        if transfer_handle in self._tracked_handles:
            raise FileAccessControlError(f"transfer handle {transfer_handle} is already tracked")
        self._tracked_handles.add(transfer_handle)
        self._status_waiters[transfer_handle] = asyncio.get_running_loop().create_future()

    def untrack_transfer(self, transfer_handle: int) -> None:
        self._tracked_handles.discard(transfer_handle)
        waiter = self._status_waiters.pop(transfer_handle, None)
        if waiter is not None and not waiter.done():
            waiter.cancel()
        self._pending_status.pop(transfer_handle, None)

    def pending_transfer_status(self, transfer_handle: int) -> IncomingTransferStatus | None:
        return self._pending_status.get(transfer_handle)

    async def cancel_transfer(self, transfer_handle: int) -> CancelTransferStatus:
        response = parse_cancel_transfer_smart_response(
            await self.protobuf.request(build_cancel_transfer_smart(CancelTransferRequest(transfer_handle)))
        )
        if response.status is None:
            raise FileAccessControlError("FileAccess cancel response is missing status")
        if response.status in (CancelTransferStatus.SUCCESS, CancelTransferStatus.UNKNOWN_TRANSFER):
            return response.status
        raise FileAccessControlError(f"FileAccess cancel failed with status {int(response.status)}")

    async def handle_incoming(self, request_id: int, serialized_smart: bytes):
        """Handle device-initiated FileAccess transfer-status requests.

        Known active transfers delay the protobuf response until the data path
        completes, matching Garmin's task lifecycle. Unknown handles receive an
        immediate empty TransferStatusResponse so the peer is not left hanging.
        """
        try:
            request = parse_transfer_status_smart_request(serialized_smart)
            if request is None:
                notification_parsers = (
                    ("item_list_cancel", parse_item_list_cancel_smart_notification),
                    ("item_added", parse_item_added_smart_notification),
                    ("item_updated", parse_item_updated_smart_notification),
                    ("resource_update", parse_resource_update_smart_notification),
                    ("sync_button", parse_sync_button_smart_notification),
                )
                for kind, parser in notification_parsers:
                    value = parser(serialized_smart)
                    if value is not None:
                        await self.events.put(FileAccessEvent(kind, value, request_id))
                        return True
                return False
        except ProtobufWireError:
            return False
        handle = request.transfer_handle
        if handle is None or handle not in self._tracked_handles:
            return build_transfer_status_smart_response()
        incoming = IncomingTransferStatus(request_id, request)
        self._pending_status[handle] = incoming
        waiter = self._status_waiters.get(handle)
        if waiter is not None and not waiter.done():
            waiter.set_result(incoming)
        return True

    async def wait_transfer_status(self, transfer_handle: int, *, timeout: float = 30.0) -> IncomingTransferStatus:
        pending = self._pending_status.get(transfer_handle)
        if pending is not None:
            return pending
        waiter = self._status_waiters.get(transfer_handle)
        if waiter is None:
            raise FileAccessControlError(f"transfer handle {transfer_handle} is not tracked")
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), timeout)
        except asyncio.TimeoutError as exc:
            raise FileAccessTransferError(
                f"timed out waiting for FileAccess transfer status {transfer_handle}"
            ) from exc

    async def respond_transfer_status(
        self,
        incoming: IncomingTransferStatus,
        *,
        next_priority: int | None = None,
    ) -> None:
        await self.protobuf.respond(
            incoming.request_id,
            build_transfer_status_smart_response(TransferStatusResponse(next_priority)),
        )
        handle = incoming.request.transfer_handle
        if handle is not None:
            self._pending_status.pop(handle, None)


class FileAccessMlrDownloader:
    """FileAccess pull over MultiLink and the reconstructed MLR ARQ engine."""

    def __init__(
        self,
        control: FileAccessControlClient,
        multilink: MultiLinkClient,
        *,
        configure_timeout: float = 10.0,
        data_timeout: float = 30.0,
        status_timeout: float = 30.0,
    ) -> None:
        if configure_timeout <= 0 or data_timeout <= 0 or status_timeout <= 0:
            raise ValueError("FileAccess timeouts must be positive")
        self.control = control
        self.multilink = multilink
        self.configure_timeout = configure_timeout
        self.data_timeout = data_timeout
        self.status_timeout = status_timeout

    @staticmethod
    def _raise_peer_failure(request: TransferStatusRequest) -> None:
        details = [f"reason={int(request.failure_reason)}"]
        if request.transport_provider_id is not None:
            details.append(f"provider={request.transport_provider_id}")
        if request.transport_status is not None:
            details.append(f"status={request.transport_status}")
        if request.transport_error_code is not None:
            details.append(f"code={request.transport_error_code}")
        raise FileAccessTransferError("device reported FileAccess transfer failure: " + ", ".join(details))

    @staticmethod
    async def _deliver_data(callback: Callable[[bytes], object] | None, chunk: bytes) -> None:
        if callback is None or not chunk:
            return
        result = callback(bytes(chunk))
        if inspect.isawaitable(result):
            await result

    async def _send_timer_actions(self, session: ReliableMlrSession, *, now_ms: float) -> bool:
        acted = False
        ack = session.poll_ack(now_ms=now_ms)
        if ack is not None:
            await self.multilink.send_raw(ack)
            acted = True
        for packet in session.retransmit_due(now_ms=now_ms):
            await self.multilink.send_raw(packet)
            acted = True
        return acted

    async def _receive_with_mlr_timers(
        self,
        service: MultiLinkService,
        session: ReliableMlrSession,
        *,
        phase_deadline: float,
        phase_name: str,
    ) -> bytes:
        """Wait for one raw packet while servicing deferred ACK and RTO timers."""
        loop = asyncio.get_running_loop()
        while True:
            now_s = loop.time()
            if now_s >= phase_deadline:
                raise FileAccessTransferError(f"timed out during FileAccess {phase_name}")
            now_ms = now_s * 1000.0
            waits = [phase_deadline - now_s]
            if session.ack_deadline_ms is not None:
                waits.append(max(0.0, (session.ack_deadline_ms - now_ms) / 1000.0))
            if session.retransmit_deadline_ms is not None:
                waits.append(max(0.0, (session.retransmit_deadline_ms - now_ms) / 1000.0))
            timeout = max(0.001, min(waits))
            try:
                return await self.multilink.recv_raw(service.handle, timeout=timeout)
            except MultiLinkClientError as exc:
                now_ms = loop.time() * 1000.0
                acted = await self._send_timer_actions(session, now_ms=now_ms)
                if loop.time() >= phase_deadline:
                    raise FileAccessTransferError(f"timed out during FileAccess {phase_name}") from exc
                if not acted:
                    raise

    async def download(
        self,
        item: FileItemReference,
        *,
        priority: int = int(TransferPriorityLevel.STANDARD_START),
        offset: int = 0,
        request_compression: bool = False,
        verify_checksum: bool = False,
        on_data: Callable[[bytes], object] | None = None,
    ) -> FileAccessDownload:
        if item.data_size is None:
            raise FileAccessTransferError("FileAccess pull requires known item size")
        if not 0 <= offset <= item.data_size:
            raise FileAccessTransferError("pull offset is outside item size")
        if verify_checksum and offset != 0:
            raise FileAccessTransferError("checksum verification of a partial pull requires resume() with the prefix")
        expected = item.data_size - offset
        negotiation = await self.control.begin_pull(
            item,
            priority=priority,
            offset=offset,
            request_compression=request_compression,
        )
        transfer_handle = negotiation.transfer_handle
        self.control.track_transfer(transfer_handle)
        service: MultiLinkService | None = None
        data = bytearray()
        decompressor = zlib.decompressobj() if negotiation.response.compression_window is not None else None
        completed = False
        checksum: FileAccessChecksum | None = None
        try:
            service = await self.multilink.open_file_transfer_service()
            loop = asyncio.get_running_loop()
            session = ReliableMlrSession(service.handle, self.multilink.max_write_length)
            configure = MlrPipeConfigure(transfer_handle, MlrPipeDirection.READ).encode()
            now_ms = loop.time() * 1000.0
            for packet in session.send_blob(configure, now_ms=now_ms):
                await self.multilink.send_raw(packet)

            configured = False
            phase_deadline = loop.time() + self.configure_timeout
            while (
                not configured
                or len(data) < expected
                or (decompressor is not None and not decompressor.eof)
            ):
                pending = self.control.pending_transfer_status(transfer_handle)
                if pending is not None and pending.request.failure_reason is not None:
                    await self.control.respond_transfer_status(pending)
                    self._raise_peer_failure(pending.request)

                raw = await self._receive_with_mlr_timers(
                    service,
                    session,
                    phase_deadline=phase_deadline,
                    phase_name="configuration" if not configured else "data transfer",
                )
                result = session.receive(raw, now_ms=loop.time() * 1000.0)
                if result.acknowledgement is not None:
                    await self.multilink.send_raw(result.acknowledgement)
                if result.data is None:
                    continue
                if not configured:
                    response = MlrPipeConfigureResponse.parse(result.data)
                    if not response.successful:
                        raise FileAccessTransferError(
                            "MultiLink FileAccess configure failed "
                            f"(general={response.general_status}, configure={response.configure_status})"
                        )
                    configured = True
                    phase_deadline = loop.time() + self.data_timeout
                    continue

                if decompressor is None:
                    produced = bytes(result.data)
                    data.extend(produced)
                    await self._deliver_data(on_data, produced)
                else:
                    try:
                        produced = decompressor.decompress(result.data)
                    except zlib.error as exc:
                        raise FileAccessTransferError("FileAccess zlib decompression failed") from exc
                    data.extend(produced)
                    await self._deliver_data(on_data, produced)
                    if decompressor.unused_data:
                        raise FileAccessTransferError("FileAccess compressed transfer contained trailing data")
                    if decompressor.eof and len(data) < expected:
                        raise FileAccessTransferError(
                            f"FileAccess compressed stream ended at {len(data)} of {expected} bytes"
                        )
                if len(data) > expected:
                    raise FileAccessTransferError(
                        f"FileAccess transfer exceeded expected size {expected}"
                    )
                phase_deadline = loop.time() + self.data_timeout

            final_ack = session.force_ack()
            if final_ack is not None:
                await self.multilink.send_raw(final_ack)

            if decompressor is not None:
                try:
                    tail = decompressor.flush()
                except zlib.error as exc:
                    raise FileAccessTransferError("FileAccess zlib finalization failed") from exc
                data.extend(tail)
                await self._deliver_data(on_data, tail)
                if not decompressor.eof:
                    raise FileAccessTransferError("FileAccess compressed stream ended before zlib end marker")
                if len(data) != expected:
                    raise FileAccessTransferError(
                        f"FileAccess decompressed size {len(data)} does not match expected {expected}"
                    )

            status = await self.control.wait_transfer_status(transfer_handle, timeout=self.status_timeout)
            await self.control.respond_transfer_status(status)
            request = status.request
            if request.failure_reason is not None:
                self._raise_peer_failure(request)

            if verify_checksum:
                checksum = await self.control.verify_item_checksum(item, bytes(data))

            completed = True
            return FileAccessDownload(
                item,
                negotiation,
                service,
                bytes(data),
                request,
                offset,
                checksum,
            )
        except BaseException:
            pending = self.control.pending_transfer_status(transfer_handle)
            if pending is not None:
                try:
                    await self.control.respond_transfer_status(pending)
                except Exception:
                    pass
            if not completed:
                try:
                    await self.control.cancel_transfer(transfer_handle)
                except Exception:
                    pass
            raise
        finally:
            if service is not None:
                try:
                    await self.multilink.close_handle(service.service_id, service.handle)
                except Exception:
                    pass
            self.control.untrack_transfer(transfer_handle)

    async def resume(
        self,
        item: FileItemReference,
        prefix: bytes,
        *,
        priority: int = int(TransferPriorityLevel.STANDARD_START),
        verify_checksum: bool = False,
        on_data: Callable[[bytes], object] | None = None,
    ) -> FileAccessDownload:
        """Resume an uncompressed pull at ``len(prefix)`` and return full bytes."""
        if item.data_size is None:
            raise FileAccessTransferError("FileAccess resume requires known item size")
        existing = bytes(prefix)
        if len(existing) >= item.data_size:
            raise FileAccessTransferError("resume prefix must be shorter than item size")
        suffix = await self.download(
            item,
            priority=priority,
            offset=len(existing),
            request_compression=False,
            verify_checksum=False,
            on_data=on_data,
        )
        full = existing + suffix.data
        if len(full) != item.data_size:
            raise FileAccessTransferError(
                f"resumed FileAccess size {len(full)} does not match expected {item.data_size}"
            )
        checksum = await self.control.verify_item_checksum(item, full) if verify_checksum else None
        return FileAccessDownload(
            item,
            suffix.negotiation,
            suffix.service,
            full,
            suffix.transfer_status,
            len(existing),
            checksum,
        )
