"""Small diagnostic CLI for offline codecs and controlled BLE experiments."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
from pathlib import Path
from dataclasses import asdict
from datetime import datetime
from typing import Sequence

from .auth_messages import PasskeyMode
from .auth_protocol import AuthProtocolEngine
from .bleak_backend import BleakBackend, BleakUnavailable
from .client import GarminClient
from .codec import DecodeProblem, DecodedPacket, GfdiWireCodec
from .device_settings import build_legacy_time_settings, build_time_updated_event
from .file_access_client import FileAccessMlrDownloader
from .fit import inspect_fit, is_activity_or_health_data_type_name
from .frame import Frame
from .handshake import HostIdentity
from .link import GfdiMessageLink
from .multilink import DEFAULT_INDEPENDENT_CLIENT_ID
from .multilink_client import MultiLinkClient
from .session import FilePairingStore, SessionPhase
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


async def _probe(args: argparse.Namespace) -> int:
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    transport = BleTransport(backend, require_bond=not args.no_bond)
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


async def _pair_only(args: argparse.Namespace) -> int:
    """Run only BLE bond + GFDI handshake/authentication and persist the LTK."""
    backend = BleakBackend(assume_bonded=args.assume_bonded)
    transport = BleTransport(backend, require_bond=not args.no_bond)
    link = GfdiMessageLink(transport, request_timeout=args.timeout)
    store = FilePairingStore(args.pairing_store)
    auth = AuthProtocolEngine(
        link,
        args.address,
        store,
        passkey_provider=_prompt_passkey,
    )
    client = GarminClient(
        link,
        HostIdentity(args.client_version, args.client_name, "Independent interoperability lab", "Python GFDI client"),
        frozenset({6, 71}),
        auth=auth,
    )
    try:
        await client.connect(DiscoveredDevice(args.address))
        await _wait_until(lambda: client.handshake.complete, args.timeout, "GFDI handshake")
        await _wait_until(
            lambda: auth.state.phase in (SessionPhase.ESTABLISHED, SessionPhase.FAILED),
            args.timeout,
            "authentication",
        )
        if auth.state.phase is SessionPhase.FAILED:
            raise RuntimeError("watch authentication failed")
        print(json.dumps({
            "status": "paired",
            "secure_session": client.session_key is not None,
            "persistent_pairing_record": store.load(args.address) is not None,
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
    transport = BleTransport(backend, require_bond=not args.no_bond)
    link = GfdiMessageLink(transport, request_timeout=args.timeout)
    store = FilePairingStore(args.pairing_store)
    auth = AuthProtocolEngine(
        link,
        args.address,
        store,
        passkey_provider=_prompt_passkey,
    )
    client = GarminClient(
        link,
        HostIdentity(args.client_version, args.client_name, "Independent interoperability lab", "Python GFDI client"),
        # Static managers used by this reference client: GNCS uses bit 6 and
        # current-time request mode uses bit 71. Next-gen FileAccess (bit 90)
        # remains explicit opt-in until its MultiLink data plane is verified.
        frozenset({6, 71} | ({90} if next_gen_requested else set())),
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

        # Authentication can be absent on some paths. If it starts, require a
        # clean terminal state before running semantic operations.
        await asyncio.sleep(0.25)
        if auth.state.phase is not SessionPhase.DISCONNECTED:
            await _wait_until(
                lambda: auth.state.phase in (SessionPhase.ESTABLISHED, SessionPhase.FAILED),
                args.timeout,
                "authentication",
            )
            if auth.state.phase is SessionPhase.FAILED:
                raise RuntimeError("watch authentication failed")

        peer_configuration = client.peer_configuration
        if (
            not args.skip_feature_capabilities
            and peer_configuration is not None
            and 95 in peer_configuration.effective_flags()
        ):
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
            if client.file_access_control is None:
                raise RuntimeError("FileAccess control client is unavailable")
            if client.file_access_capabilities is None:
                raise RuntimeError(
                    "next-gen files requested but the watch did not advertise FileAccess capabilities"
                )
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

    probe = sub.add_parser("probe", help="connect and establish only the raw GFDI BLE link")
    probe.add_argument("address")
    probe.add_argument("--no-bond", action="store_true")
    probe.add_argument("--assume-bonded", action="store_true")
    probe.add_argument("--show-address", action="store_true")
    probe.set_defaults(func=lambda args: _run_hardware(_probe(args)))

    pair = sub.add_parser("pair", help="run only BLE bond + GFDI authentication and persist pairing material")
    pair.add_argument("address")
    pair.add_argument("--pairing-store", default=str(Path.home() / ".local" / "share" / "garmin-proto" / "pairing.json"))
    pair.add_argument("--client-version", type=int, default=1)
    pair.add_argument("--client-name", default="Independent Garmin Client")
    pair.add_argument("--timeout", type=float, default=45.0)
    pair.add_argument("--no-bond", action="store_true")
    pair.add_argument("--assume-bonded", action="store_true")
    pair.set_defaults(func=lambda args: _run_hardware(_pair_only(args)))

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
    workflow.add_argument("--next-gen-files", action="store_true", help="list activity/health items through experimental FileAccess protobuf")
    workflow.add_argument("--download-first-next-gen-fitness", action="store_true", help="download the first next-gen activity/health item over experimental MultiLink/MLR")
    workflow.add_argument("--next-gen-compression", action="store_true", help="request statically reconstructed zlib compression for next-gen pull")
    workflow.add_argument(
        "--multilink-client-id",
        type=lambda value: int(value, 0),
        default=DEFAULT_INDEPENDENT_CLIENT_ID,
        help="stable nonzero application-defined MultiLink client ID (default wire bytes: GPLAB001)",
    )
    workflow.add_argument("--gncs-version", default="0.1.0", help="independent GNCS semantic version advertised to capable watches")
    workflow.add_argument("--skip-time", action="store_true")
    workflow.add_argument("--skip-files", action="store_true")
    workflow.add_argument("--download-first-activity", action="store_true")
    workflow.add_argument("--archive-after-download", action="store_true")
    workflow.add_argument("--no-bond", action="store_true")
    workflow.add_argument("--assume-bonded", action="store_true")
    workflow.set_defaults(func=lambda args: _run_hardware(_workflow(args)))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
