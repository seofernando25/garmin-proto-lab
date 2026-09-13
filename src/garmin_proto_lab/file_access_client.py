"""Next-generation GDI FileAccess client and experimental MultiLink pull path.

The protobuf control plane and MultiLink wire grammar are independently
reconstructed. The pull data path deliberately starts uncompressed and uses a
conservative immediate-ACK MLR policy; hardware verification is still required.
"""
from __future__ import annotations

import asyncio
import zlib
from dataclasses import dataclass
from uuid import UUID

from .file_access_proto import (
    CancelTransferRequest,
    CancelTransferStatus,
    FileDataType,
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
    build_item_list_smart,
    build_pull_item_smart,
    build_transfer_status_smart_response,
    parse_cancel_transfer_smart_response,
    parse_item_list_smart_response,
    parse_pull_item_smart_response,
    parse_transfer_status_smart_request,
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
class FileAccessDownload:
    item: FileItemReference
    negotiation: PullNegotiation
    service: MultiLinkService
    data: bytes
    transfer_status: TransferStatusRequest


class FileAccessControlClient:
    def __init__(self, protobuf: ProtobufSmartLink, *, max_pages: int = 256) -> None:
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.protobuf = protobuf
        self.max_pages = max_pages
        self._tracked_handles: set[int] = set()
        self._status_waiters: dict[int, asyncio.Future[IncomingTransferStatus]] = {}
        self._pending_status: dict[int, IncomingTransferStatus] = {}

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
        except ProtobufWireError:
            return False
        if request is None:
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
    """Experimental FileAccess pull over clean-room MultiLink/MLR.

    Uncompressed transfer remains the default. Optional compression uses the
    standard zlib stream implied by Garmin's ``Inflater`` read-side wrapper.
    """

    def __init__(
        self,
        control: FileAccessControlClient,
        multilink: MultiLinkClient,
        *,
        configure_timeout: float = 10.0,
        data_timeout: float = 30.0,
        status_timeout: float = 30.0,
        configure_retries: int = 2,
    ) -> None:
        if configure_retries < 0:
            raise ValueError("configure_retries cannot be negative")
        self.control = control
        self.multilink = multilink
        self.configure_timeout = configure_timeout
        self.data_timeout = data_timeout
        self.status_timeout = status_timeout
        self.configure_retries = configure_retries

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

    async def download(
        self,
        item: FileItemReference,
        *,
        priority: int = int(TransferPriorityLevel.STANDARD_START),
        offset: int = 0,
        request_compression: bool = False,
    ) -> FileAccessDownload:
        if item.data_size is None:
            raise FileAccessTransferError("FileAccess pull requires known item size")
        if not 0 <= offset <= item.data_size:
            raise FileAccessTransferError("pull offset is outside item size")
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
        status: IncomingTransferStatus | None = None
        data = bytearray()
        decompressor = zlib.decompressobj() if negotiation.response.compression_window is not None else None
        completed = False
        try:
            service = await self.multilink.open_file_transfer_service()
            session = ReliableMlrSession(service.handle, self.multilink.max_write_length)
            configure = MlrPipeConfigure(transfer_handle, MlrPipeDirection.READ).encode()
            for packet in session.send_blob(configure):
                await self.multilink.send_raw(packet)

            configured = False
            retries = self.configure_retries
            while (
                not configured
                or len(data) < expected
                or (decompressor is not None and not decompressor.eof)
            ):
                timeout = self.configure_timeout if not configured else self.data_timeout
                try:
                    raw = await self.multilink.recv_raw(service.handle, timeout=timeout)
                except MultiLinkClientError:
                    pending = self.control.pending_transfer_status(transfer_handle)
                    if pending is not None and pending.request.failure_reason is not None:
                        await self.control.respond_transfer_status(pending)
                        self._raise_peer_failure(pending.request)
                    if not configured and retries > 0 and session.outstanding_count:
                        retries -= 1
                        for packet in session.retransmit_outstanding():
                            await self.multilink.send_raw(packet)
                        continue
                    raise
                result = session.receive(raw)
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
                    continue
                if decompressor is None:
                    data.extend(result.data)
                else:
                    try:
                        data.extend(decompressor.decompress(result.data))
                    except zlib.error as exc:
                        raise FileAccessTransferError("FileAccess zlib decompression failed") from exc
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

            if decompressor is not None:
                try:
                    data.extend(decompressor.flush())
                except zlib.error as exc:
                    raise FileAccessTransferError("FileAccess zlib finalization failed") from exc
                if not decompressor.eof:
                    raise FileAccessTransferError("FileAccess compressed stream ended before zlib end marker")
                if len(data) != expected:
                    raise FileAccessTransferError(
                        f"FileAccess decompressed size {len(data)} does not match expected {expected}"
                    )

            status = await self.control.wait_transfer_status(transfer_handle, timeout=self.status_timeout)
            # Always answer the status request before surfacing a peer-reported
            # failure; Garmin's manager also completes the request/response pair.
            await self.control.respond_transfer_status(status)
            request = status.request
            if request.failure_reason is not None:
                self._raise_peer_failure(request)

            completed = True
            assert service is not None
            return FileAccessDownload(item, negotiation, service, bytes(data), request)
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
