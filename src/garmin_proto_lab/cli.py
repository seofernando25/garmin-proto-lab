"""Small diagnostic CLI for offline codecs and controlled BLE experiments."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
from dataclasses import asdict
from datetime import datetime
from typing import Sequence

from .auth_messages import PasskeyMode
from .auth_protocol import AuthProtocolEngine
from .bleak_backend import BleakBackend, BleakUnavailable
from .client import GarminClient
from .capabilities import FITNESS_HOST_FLAGS, PAIRING_HOST_FLAGS, workflow_host_flags
from .codec import DecodeProblem, DecodedPacket, GfdiWireCodec
from .device_settings import build_legacy_time_settings, build_time_updated_event
from .file_access_client import FileAccessMlrDownloader
from .fit import fit_semantic_summary, inspect_fit, is_activity_or_health_data_type_name
from .frame import Frame
from .fitness_sync import NextGenFitnessSync
from .handshake import HostIdentity
from .link import GfdiMessageLink
from .multilink import DEFAULT_INDEPENDENT_CLIENT_ID
from .multilink_client import MultiLinkClient
from .session import FilePairingStore, SessionPhase, requires_system_bond
from .transport import BleTransport, DiscoveredDevice, candidate_score


def _redact(value: str, show: bool) -> str:
    if show:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"device-{digest}"


def _parse_hex(text: str) -> bytes:
    normalized = "".join(text.split()).replace(":", "")
    try:
        return bytes.fromhex(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid hexadecimal bytes: {exc}") from exc


def _cmd_decode(args: argparse.Namespace) -> int:
    codec = GfdiWireCodec()
    results = codec.feed(args.wire)
    if not results:
        print(json.dumps({"status": "incomplete", "bytes": len(args.wire)}))
        return 2
    status = 0
    for result in results:
        if isinstance(result, DecodeProblem):
            print(json.dumps({"status": "error", "stage": result.stage, "reason": result.reason}))
            status = 1
        else:
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "message_type": result.frame.message_type,
                        "transaction_id": result.frame.transaction_id,
                        "payload_hex": result.frame.payload.hex(),
                        "inner_length": len(result.inner_frame_bytes),
                        "outer_length": len(result.outer_packet_bytes),
                    }
                )
            )
    return status


def _cmd_encode(args: argparse.Namespace) -> int:
    frame = Frame(args.message_type, args.payload, args.transaction)
    print(GfdiWireCodec().encode(frame).hex())
    return 0


def _write_json_private(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    temporary.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _fit_output_metadata(path: Path, *, write_fit_json: bool) -> dict[str, object]:
    data = path.read_bytes()
    summary = fit_semantic_summary(
        data,
        include_activity_samples=write_fit_json,
        include_activity_summaries=write_fit_json,
        include_wellness_samples=write_fit_json,
        include_records=write_fit_json,
    )
    output: dict[str, object] = {
        "record_count": summary["record_count"],
        "activity_sample_count": summary["activity_sample_count"],
    }
    if write_fit_json:
        sidecar = path.with_suffix(path.suffix + ".json")
        _write_json_private(sidecar, summary)
        output["fit_json"] = str(sidecar)
    return output


def _cmd_time(args: argparse.Namespace) -> int:
    now = datetime.now().astimezone()
    print(
        json.dumps(
            {
                "local_time": now.isoformat(),
                "legacy_5026_hex": build_legacy_time_settings(now).hex(),
                "time_updated_5030_hex": build_time_updated_event().hex(),
            },
            indent=2,
        )
    )
    return 0


async def _scan(args: argparse.Namespace) -> int:
    backend = BleakBackend()
    # Use the backend directly so a diagnostic scan can expose devices that do
    # not advertise a Garmin service UUID; candidate_score is shown separately.
    devices = await backend.scan(args.seconds)
    rows = []
    for device in sorted(devices, key=lambda item: (-(item.rssi or -10000), item.address)):
        score = candidate_score(device)
        if not args.all and score <= 0:
            continue
        rows.append(
            {
                "device": _redact(device.address, args.show_address),
                "name": device.name,
                "rssi": device.rssi,
                "garmin_candidate_score": score,
                "advertised_services": [str(value) for value in sorted(device.service_uuids, key=str)],
            }
        )
    print(json.dumps(rows, indent=2))
    return 0


async def _services(args: argparse.Namespace) -> int:
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    device = DiscoveredDevice(args.address)
    try:
        await backend.connect(device)
        services = await backend.discover_services()
        mtu = await backend.request_mtu(515)
        output = {
            "device": _redact(args.address, args.show_address),
            "mtu": mtu,
            "services": [
                {
                    "uuid": str(service.uuid),
                    "characteristics": [
                        {"uuid": str(characteristic.uuid), "properties": sorted(characteristic.properties)}
                        for characteristic in service.characteristics
                    ],
                }
                for service in services
            ],
        }
        print(json.dumps(output, indent=2))
        return 0
    finally:
        await backend.disconnect()


async def _multilink_info(args: argparse.Namespace) -> int:
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    device = DiscoveredDevice(args.address)
    try:
        if args.system_bond:
            await backend.connect_with_pairing(device)
        else:
            await backend.connect(device)
        services = tuple(await backend.discover_services())
        mtu = await backend.request_mtu(515)
        multilink = MultiLinkClient(
            backend,
            services,
            args.multilink_client_id,
            max(20, mtu - 3),
            timeout=args.timeout,
        )
        await multilink.initialize()
        info = await multilink.query_registration_info()
        gfdi_revision = None
        if info.supported_services and 1 in info.supported_services:
            gfdi_revision = await multilink.query_service_revision(1)
        unit_id = info.product.unit_id if info.product is not None else None
        identity = info.identity_address
        output = {
            "device": _redact(args.address, args.show_identifiers),
            "mtu": mtu,
            "supported_services": sorted(info.supported_services or ()),
            "advertising_service_data_hex": (
                info.advertising_service_data.hex()
                if info.advertising_service_data is not None
                else None
            ),
            "multi_link_version": (
                f"{info.version.major}.{info.version.minor}.{info.version.micro}"
                if info.version is not None
                else None
            ),
            "product_number": info.product.product_number if info.product is not None else None,
            "firmware_version": info.product.firmware_version if info.product is not None else None,
            "unit_id": (
                unit_id
                if args.show_identifiers or unit_id is None
                else _redact(str(unit_id), False)
            ),
            "identity_address": (
                identity.hex()
                if args.show_identifiers and identity is not None
                else _redact(identity.hex(), False)
                if identity is not None
                else None
            ),
            "gfdi_service_revision": gfdi_revision,
        }
        print(json.dumps(output, indent=2))
        return 0
    finally:
        await backend.disconnect()


async def _probe(args: argparse.Namespace) -> int:
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    transport = BleTransport(backend, require_bond=args.system_bond)
    received = 0

    async def notification(data: bytes) -> None:
        nonlocal received
        received += len(data)

    try:
        link = await transport.connect(DiscoveredDevice(args.address), notification)
        print(
            json.dumps(
                {
                    "device": _redact(args.address, args.show_address),
                    "state": transport.state.value,
                    "service_uuid": str(link.service_uuid),
                    "write_uuid": str(link.write_uuid),
                    "notify_uuid": str(link.notify_uuid),
                    "family": link.family,
                    "mtu": transport.negotiated_mtu,
                    "write_payload_size": transport.write_payload_size,
                    "notification_bytes_seen": received,
                },
                indent=2,
            )
        )
        return 0
    finally:
        await transport.disconnect()


async def _prompt_passkey(mode: PasskeyMode, timeout_seconds: int):
    if mode is PasskeyMode.VISIBLE:
        prompt = f"Enter the decimal passkey shown by the watch (timeout about {timeout_seconds}s): "
        return await asyncio.to_thread(getpass.getpass, prompt)
    if mode is PasskeyMode.OUT_OF_BAND:
        prompt = "Enter the 16-byte OOB passkey as 32 hexadecimal characters: "
        text = await asyncio.to_thread(getpass.getpass, prompt)
        try:
            value = bytes.fromhex(text.strip())
        except ValueError as exc:
            raise RuntimeError("invalid OOB hexadecimal passkey") from exc
        if len(value) != 16:
            raise RuntimeError("OOB passkey must be exactly 16 bytes")
        return value
    return bytes(16)


async def _wait_until(predicate, timeout: float, description: str) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise TimeoutError(f"timed out waiting for {description}")
        await asyncio.sleep(0.05)


async def _finish_pairing_state(
    auth: AuthProtocolEngine,
    transport: BleTransport,
    backend: BleakBackend,
    timeout: float,
) -> str:
    """Resolve the bonded StubAuth route versus proprietary Garmin auth."""
    loop = asyncio.get_running_loop()
    if auth.state.phase is SessionPhase.DISCONNECTED:
        # The unbonded route must receive Garmin Auth negotiation, but it may
        # arrive after the 5024/5050 handshake callback has completed. Give it
        # the full pairing timeout rather than assuming a fixed scheduling gap.
        start_timeout = min(timeout, 1.0) if transport.require_bond else timeout
        deadline = loop.time() + start_timeout
        while auth.state.phase is SessionPhase.DISCONNECTED and loop.time() < deadline:
            await asyncio.sleep(0.05)

    if auth.state.phase is not SessionPhase.DISCONNECTED:
        await _wait_until(
            lambda: auth.state.phase in (SessionPhase.ESTABLISHED, SessionPhase.FAILED),
            timeout,
            "authentication",
        )
    if auth.state.phase is SessionPhase.FAILED:
        raise RuntimeError("watch authentication failed")
    if auth.state.phase is SessionPhase.ESTABLISHED:
        return "garmin_auth"
    if transport.require_bond:
        if not await backend.is_bonded():
            raise RuntimeError("system Bluetooth bond did not persist")
        return "system_bond"
    raise RuntimeError("Garmin authentication did not start on an unbonded connection")


async def _reset_pairing(args: argparse.Namespace) -> int:
    """Remove persisted GFDI auth material and, by default, the OS bond."""
    backend = BleakBackend()
    store = FilePairingStore(args.pairing_store)
    system_bond_removed = False
    if not args.keep_system_bond:
        await backend.remove_bond(DiscoveredDevice(args.address))
        system_bond_removed = True
    store.delete(args.address)
    print(json.dumps({
        "status": "pairing_reset",
        "system_bond_removed": system_bond_removed,
        "garmin_auth_removed": True,
    }, indent=2))
    return 0


async def _pair_only(args: argparse.Namespace) -> int:
    """Run GFDI handshake/authentication and persist the LTK."""
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    store = FilePairingStore(args.pairing_store)
    transport = BleTransport(
        backend,
        require_bond=requires_system_bond(
            store,
            args.address,
            force=args.system_bond,
            force_garmin_auth=args.garmin_auth,
        ),
    )
    link = GfdiMessageLink(transport, request_timeout=args.timeout)
    auth = AuthProtocolEngine(
        link,
        args.address,
        store,
        passkey_provider=_prompt_passkey,
    )
    client = GarminClient(
        link,
        HostIdentity(args.client_version, args.client_name, "Independent interoperability lab", "Python GFDI client"),
        PAIRING_HOST_FLAGS,
        auth=auth,
    )
    try:
        await client.connect(DiscoveredDevice(args.address))
        await _wait_until(lambda: client.handshake.complete, args.timeout, "GFDI handshake")
        pairing_mode = await _finish_pairing_state(auth, transport, backend, args.timeout)
        print(json.dumps({
            "status": "paired",
            "pairing_mode": pairing_mode,
            "system_bond_required": transport.require_bond,
            "secure_session": client.session_key is not None,
            "persistent_garmin_auth_record": store.load(args.address) is not None,
        }, indent=2))
        return 0
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def _workflow(args: argparse.Namespace) -> int:
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    next_gen_requested = bool(
        args.enable_next_gen_file_access
        or args.next_gen_files
        or args.download_first_next_gen_fitness
    )
    store = FilePairingStore(args.pairing_store)
    transport = BleTransport(
        backend,
        require_bond=requires_system_bond(
            store,
            args.address,
            force=args.system_bond,
            force_garmin_auth=args.garmin_auth,
        ),
        multilink_connection_id=args.multilink_client_id,
    )
    link = GfdiMessageLink(transport, request_timeout=args.timeout)
    auth = AuthProtocolEngine(
        link,
        args.address,
        store,
        passkey_provider=_prompt_passkey,
    )
    client = GarminClient(
        link,
        HostIdentity(args.client_version, args.client_name, "Independent interoperability lab", "Python GFDI client"),
        workflow_host_flags(notifications=True, file_access=next_gen_requested),
        auth=auth,
    )

    async def event_printer() -> None:
        while True:
            event = await client.events.get()
            print(json.dumps({"event": event.kind.value, "text": event.accessible_text(), "message_type": event.message_type}))

    printer = asyncio.create_task(event_printer())
    try:
        await client.connect(DiscoveredDevice(args.address))
        await _wait_until(lambda: client.handshake.complete, args.timeout, "GFDI handshake")

        await _finish_pairing_state(auth, transport, backend, args.timeout)

        peer_configuration = client.peer_configuration
        if not args.skip_feature_capabilities and client.peer_supports_feature_capabilities:
            capabilities = await client.refresh_feature_capabilities(gncs_version=args.gncs_version)
            print(
                json.dumps(
                    {
                        "feature_capabilities": {
                            "guid_status": int(capabilities.guid_status) if capabilities.guid_status is not None else None,
                            "version": capabilities.version,
                            "gncs_nc_version": capabilities.gncs.nc_version if capabilities.gncs else None,
                            "gncs_support_blocked_apps": capabilities.gncs.support_blocked_apps if capabilities.gncs else None,
                            "file_access": (
                                {
                                    "checksum_method": int(client.file_access_capabilities.server_file_checksum_method)
                                    if client.file_access_capabilities and client.file_access_capabilities.server_file_checksum_method is not None
                                    else None,
                                    "checksum_max_file_size": client.file_access_capabilities.checksum_max_file_size_byte
                                    if client.file_access_capabilities
                                    else None,
                                    "custom_flags": client.file_access_capabilities.custom_flag_support
                                    if client.file_access_capabilities
                                    else None,
                                }
                                if client.file_access_capabilities is not None
                                else None
                            ),
                        }
                    },
                    indent=2,
                )
            )

        if args.next_gen_files or args.download_first_next_gen_fitness:
            if not client.peer_supports_file_access:
                raise RuntimeError("next-gen files requested but the watch did not advertise Configuration bit 90")
            if client.file_access_control is None:
                raise RuntimeError("FileAccess control client is unavailable")
            listing = await client.file_access_control.list_items()
            named = list(zip(listing.items, listing.data_type_names, strict=False))
            candidates = [
                (item, name)
                for item, name in named
                if is_activity_or_health_data_type_name(name)
            ]
            print(
                json.dumps(
                    {
                        "next_gen_activity_health_candidates": [
                            {
                                "data_type": name,
                                "size": item.data_size,
                                "urgency": int(item.urgency) if item.urgency is not None else None,
                            }
                            for item, name in candidates
                        ],
                        "next_transaction_id": listing.next_transaction_id,
                    },
                    indent=2,
                )
            )
            if args.download_first_next_gen_fitness and candidates:
                multilink = transport.multilink_client
                if multilink is None:
                    multilink = MultiLinkClient(
                        backend,
                        transport.services,
                        args.multilink_client_id,
                        transport.write_payload_size,
                        timeout=min(args.timeout, 10.0),
                    )
                    await multilink.initialize()
                downloaded = await FileAccessMlrDownloader(
                    client.file_access_control,
                    multilink,
                    configure_timeout=min(args.timeout, 10.0),
                    data_timeout=args.timeout,
                    status_timeout=args.timeout,
                ).download(
                    candidates[0][0],
                    request_compression=args.next_gen_compression,
                )
                fit = inspect_fit(downloaded.data)
                print(
                    json.dumps(
                        {
                            "next_gen_download": {
                                "bytes": len(downloaded.data),
                                "fit_type": int(fit.file_type_raw) if fit.file_type_raw is not None else None,
                                "multilink_service": downloaded.service.service_id,
                                "multilink_handle": downloaded.service.handle,
                            }
                        },
                        indent=2,
                    )
                )

        if not args.skip_battery:
            await client.send_phone_battery(args.battery_percent)
        if not args.skip_time:
            await client.sync_time(request_timeout=min(args.timeout, 10.0))

        if not args.skip_files:
            candidates = await client.list_activity_health_files()
            print(
                json.dumps(
                    {
                        "activity_health_candidates": [
                            {
                                "file_index": item.entry.file_index,
                                "data_type": item.entry.data_type,
                                "subtype": item.entry.subtype,
                                "type_name": item.type_name,
                                "size": item.entry.size,
                            }
                            for item in candidates
                        ]
                    },
                    indent=2,
                )
            )
            if args.download_first_activity and candidates:
                transfer = await client.download_activity_health(
                    candidates[0],
                    archive_after_success=args.archive_after_download,
                )
                print(
                    json.dumps(
                        {
                            "downloaded_file_index": transfer.remote.entry.file_index,
                            "bytes": transfer.file.transferred_size,
                            "fit_type": int(transfer.fit.file_type_raw) if transfer.fit.file_type_raw is not None else None,
                            "archived": bool(args.archive_after_download),
                        }
                    )
                )

        print(json.dumps({"status": client.status_snapshot()}, indent=2))
        return 0
    finally:
        printer.cancel()
        try:
            await printer
        except asyncio.CancelledError:
            pass
        try:
            await client.disconnect()
        except Exception:
            pass


async def _fitness_sync(args: argparse.Namespace) -> int:
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    store = FilePairingStore(args.pairing_store)
    transport = BleTransport(
        backend,
        require_bond=requires_system_bond(
            store,
            args.address,
            force=args.system_bond,
            force_garmin_auth=args.garmin_auth,
        ),
        multilink_connection_id=args.multilink_client_id,
    )
    link = GfdiMessageLink(transport, request_timeout=args.timeout)
    auth = AuthProtocolEngine(
        link,
        args.address,
        store,
        passkey_provider=_prompt_passkey,
    )
    client = GarminClient(
        link,
        HostIdentity(args.client_version, args.client_name, "Independent interoperability lab", "Python GFDI client"),
        FITNESS_HOST_FLAGS,
        auth=auth,
    )
    output_dir = Path(args.output).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        output_dir.chmod(0o700)
    except OSError:
        pass

    try:
        await client.connect(DiscoveredDevice(args.address))
        await _wait_until(lambda: client.handshake.complete, args.timeout, "GFDI handshake")
        await _finish_pairing_state(auth, transport, backend, args.timeout)

        peer_configuration = client.peer_configuration
        next_gen_available = not args.legacy and client.peer_supports_file_access
        if next_gen_available and client.peer_supports_feature_capabilities:
            await client.refresh_feature_capabilities()

        if next_gen_available:
            assert client.file_access_control is not None
            multilink = transport.multilink_client
            if multilink is None:
                multilink = MultiLinkClient(
                    backend,
                    transport.services,
                    args.multilink_client_id,
                    transport.write_payload_size,
                    timeout=min(args.timeout, 10.0),
                )
                await multilink.initialize()
            downloader = FileAccessMlrDownloader(
                client.file_access_control,
                multilink,
                configure_timeout=min(args.timeout, 10.0),
                data_timeout=args.timeout,
                status_timeout=args.timeout,
            )
            synchronizer = NextGenFitnessSync(
                client.file_access_control,
                downloader,
                output_dir,
                capabilities=client.file_access_capabilities,
                request_compression=args.compression,
            )
            results = await synchronizer.sync(limit=args.limit)
            files = []
            for result in results:
                metadata = _fit_output_metadata(
                    result.path,
                    write_fit_json=args.fit_json,
                )
                files.append({
                    "transport": "file_access",
                    "data_type": result.data_type_name,
                    "path": str(result.path),
                    "bytes": result.item.data_size,
                    "fit_type": int(result.inspection.file_type_raw)
                    if result.inspection.file_type_raw is not None
                    else None,
                    "resumed_from": result.resumed_from,
                    "checksum_verified": result.checksum_verified,
                    "fit_crc_verified": True,
                    "already_present": result.already_present,
                    **metadata,
                })
        else:
            candidates = list(await client.list_activity_health_files())
            if args.limit is not None:
                candidates = candidates[: args.limit]
            files = []
            for remote in candidates:
                transfer = await client.download_activity_health(
                    remote,
                    compression=args.compression,
                    archive_after_success=args.archive_legacy_after_sync,
                )
                digest = hashlib.sha256(transfer.file.data).hexdigest()[:16]
                path = output_dir / f"fit-type-{remote.entry.subtype}-{digest}.fit"
                already_present = path.exists()
                if not already_present:
                    temporary = path.with_suffix(path.suffix + ".tmp")
                    temporary.write_bytes(transfer.file.data)
                    try:
                        temporary.chmod(0o600)
                    except OSError:
                        pass
                    temporary.replace(path)
                metadata = _fit_output_metadata(
                    path,
                    write_fit_json=args.fit_json,
                )
                files.append(
                    {
                        "transport": "legacy_gfdi_file",
                        "data_type": remote.type_name,
                        "path": str(path),
                        "bytes": len(transfer.file.data),
                        "fit_type": int(transfer.fit.file_type_raw)
                        if transfer.fit.file_type_raw is not None
                        else None,
                        "resumed_from": 0,
                        "checksum_verified": False,
                        "fit_crc_verified": True,
                        "already_present": already_present,
                        "archived": bool(args.archive_legacy_after_sync),
                        **metadata,
                    }
                )

        print(json.dumps({"status": "complete", "files": files}, indent=2))
        return 0
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def _run_hardware(coro) -> int:
    try:
        return asyncio.run(coro)
    except BleakUnavailable as exc:
        print(f"hardware backend unavailable: {exc}")
        return 3
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="garmin-proto", description="Independent Garmin GFDI protocol lab")
    sub = parser.add_subparsers(dest="command", required=True)

    decode = sub.add_parser("decode", help="decode zero-delimited COBS/GFDI wire bytes")
    decode.add_argument("wire", type=_parse_hex)
    decode.set_defaults(func=lambda args: _cmd_decode(args))

    encode = sub.add_parser("encode", help="encode one plaintext GFDI frame to COBS wire bytes")
    encode.add_argument("message_type", type=int)
    encode.add_argument("payload", nargs="?", default=b"", type=_parse_hex)
    encode.add_argument("--transaction", type=int)
    encode.set_defaults(func=lambda args: _cmd_encode(args))

    time_cmd = sub.add_parser("time-payloads", help="show current legacy/new time-sync payloads")
    time_cmd.set_defaults(func=lambda args: _cmd_time(args))

    scan = sub.add_parser("scan", help="scan BLE advertisements; requires hardware extra")
    scan.add_argument("--seconds", type=float, default=8.0)
    scan.add_argument("--all", action="store_true", help="include non-Garmin-scored advertisements")
    scan.add_argument("--show-address", action="store_true", help="show raw device addresses instead of hashes")
    scan.set_defaults(func=lambda args: _run_hardware(_scan(args)))

    services = sub.add_parser("services", help="dump GATT service/characteristic metadata")
    services.add_argument("address")
    services.add_argument("--assume-bonded", action="store_true")
    services.add_argument("--show-address", action="store_true")
    services.set_defaults(func=lambda args: _run_hardware(_services(args)))

    multilink_info = sub.add_parser("multilink-info", help="query MultiLink registration pages and service support")
    multilink_info.add_argument("address")
    multilink_info.add_argument("--timeout", type=float, default=10.0)
    multilink_info.add_argument("--system-bond", action="store_true", help="pair through BlueZ before querying MultiLink")
    multilink_info.add_argument("--assume-bonded", action="store_true")
    multilink_info.add_argument("--show-identifiers", action="store_true", help="show unit/identity values instead of hashes")
    multilink_info.add_argument(
        "--multilink-client-id",
        type=lambda value: int(value, 0),
        default=DEFAULT_INDEPENDENT_CLIENT_ID,
        help="MultiLink client ID (default 0x01 from Garmin Connect client_config.xml)",
    )
    multilink_info.set_defaults(func=lambda args: _run_hardware(_multilink_info(args)))

    probe = sub.add_parser("probe", help="connect and establish only the raw GFDI BLE link")
    probe.add_argument("address")
    probe.add_argument("--system-bond", action="store_true", help="force an Android/OS Bluetooth bond")
    probe.add_argument("--assume-bonded", action="store_true")
    probe.add_argument("--show-address", action="store_true")
    probe.set_defaults(func=lambda args: _run_hardware(_probe(args)))

    reset_pairing = sub.add_parser(
        "reset-pairing",
        help="remove saved Garmin authentication and the OS Bluetooth bond for a fresh pairing run",
    )
    reset_pairing.add_argument("address")
    reset_pairing.add_argument("--pairing-store", default=str(Path.home() / ".local" / "share" / "garmin-proto" / "pairing.json"))
    reset_pairing.add_argument("--keep-system-bond", action="store_true", help="delete only the saved Garmin LTK/EDIV/RAND record")
    reset_pairing.set_defaults(func=lambda args: _run_hardware(_reset_pairing(args)))

    pair = sub.add_parser("pair", help="run GFDI authentication and persist pairing material")
    pair.add_argument("address")
    pair.add_argument("--pairing-store", default=str(Path.home() / ".local" / "share" / "garmin-proto" / "pairing.json"))
    pair.add_argument("--client-version", type=int, default=1)
    pair.add_argument("--client-name", default="Independent Garmin Client")
    pair.add_argument("--timeout", type=float, default=45.0)
    pair_mode = pair.add_mutually_exclusive_group()
    pair_mode.add_argument("--system-bond", action="store_true", help="force an Android/OS Bluetooth bond")
    pair_mode.add_argument("--garmin-auth", action="store_true", help="force the unbonded Garmin 5101..5111 authentication path")
    pair.add_argument("--assume-bonded", action="store_true")
    pair.set_defaults(func=lambda args: _run_hardware(_pair_only(args)))

    fitness_sync = sub.add_parser(
        "fitness-sync",
        help="pair/connect and synchronize activity, monitoring, and sleep FIT files directly from the watch",
    )
    fitness_sync.add_argument("address")
    fitness_sync.add_argument("--output", required=True, help="directory for verified FIT files and resumable .part files")
    fitness_sync.add_argument("--pairing-store", default=str(Path.home() / ".local" / "share" / "garmin-proto" / "pairing.json"))
    fitness_sync.add_argument("--client-version", type=int, default=1)
    fitness_sync.add_argument("--client-name", default="Independent Garmin Client")
    fitness_sync.add_argument("--timeout", type=float, default=45.0)
    fitness_sync.add_argument("--limit", type=int, help="maximum number of FileAccess fitness items to synchronize")
    fitness_sync.add_argument("--compression", action="store_true", help="request compressed transfer when the selected file transport supports it")
    fitness_sync.add_argument("--legacy", action="store_true", help="force the legacy GFDI file-transfer path instead of FileAccess")
    fitness_sync.add_argument("--archive-legacy-after-sync", action="store_true", help="set the legacy archive flag only after a verified download")
    fitness_sync.add_argument(
        "--fit-json",
        "--activity-json",
        dest="fit_json",
        action="store_true",
        help="write a private JSON sidecar with generic FIT records plus decoded standard activity samples",
    )
    fitness_pair_mode = fitness_sync.add_mutually_exclusive_group()
    fitness_pair_mode.add_argument("--system-bond", action="store_true", help="force an Android/OS Bluetooth bond")
    fitness_pair_mode.add_argument("--garmin-auth", action="store_true", help="force the unbonded Garmin 5101..5111 authentication path")
    fitness_sync.add_argument("--assume-bonded", action="store_true")
    fitness_sync.add_argument(
        "--multilink-client-id",
        type=lambda value: int(value, 0),
        default=DEFAULT_INDEPENDENT_CLIENT_ID,
        help="MultiLink client ID (default 0x01 from Garmin Connect client_config.xml)",
    )
    fitness_sync.set_defaults(func=lambda args: _run_hardware(_fitness_sync(args)))

    workflow = sub.add_parser("workflow", help="run the independent handshake/auth/time/file workflow on a watch")
    workflow.add_argument("address")
    workflow.add_argument("--pairing-store", default=str(Path.home() / ".local" / "share" / "garmin-proto" / "pairing.json"))
    workflow.add_argument("--client-version", type=int, default=1)
    workflow.add_argument("--client-name", default="Independent Garmin Client")
    workflow.add_argument("--timeout", type=float, default=45.0)
    workflow.add_argument("--battery-percent", type=int, default=100)
    workflow.add_argument("--skip-battery", action="store_true")
    workflow.add_argument("--skip-feature-capabilities", action="store_true")
    workflow.add_argument("--enable-next-gen-file-access", action="store_true", help="advertise statically reconstructed FileAccess config bit 90")
    workflow.add_argument("--next-gen-files", action="store_true", help="list activity/health items through FileAccess protobuf")
    workflow.add_argument("--download-first-next-gen-fitness", action="store_true", help="download the first next-gen activity/health item over MultiLink/MLR")
    workflow.add_argument("--next-gen-compression", action="store_true", help="request statically reconstructed zlib compression for next-gen pull")
    workflow.add_argument(
        "--multilink-client-id",
        type=lambda value: int(value, 0),
        default=DEFAULT_INDEPENDENT_CLIENT_ID,
        help="MultiLink client ID (default 0x01 from Garmin Connect client_config.xml)",
    )
    workflow.add_argument("--gncs-version", default="0.1.0", help="independent GNCS semantic version advertised to capable watches")
    workflow.add_argument("--skip-time", action="store_true")
    workflow.add_argument("--skip-files", action="store_true")
    workflow.add_argument("--download-first-activity", action="store_true")
    workflow.add_argument("--archive-after-download", action="store_true")
    workflow_pair_mode = workflow.add_mutually_exclusive_group()
    workflow_pair_mode.add_argument("--system-bond", action="store_true", help="force an Android/OS Bluetooth bond")
    workflow_pair_mode.add_argument("--garmin-auth", action="store_true", help="force the unbonded Garmin 5101..5111 authentication path")
    workflow.add_argument("--assume-bonded", action="store_true")
    workflow.set_defaults(func=lambda args: _run_hardware(_workflow(args)))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
