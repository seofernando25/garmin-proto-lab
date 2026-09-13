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
from .frame import Frame
from .handshake import HostIdentity
from .link import GfdiMessageLink
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


async def _workflow(args: argparse.Namespace) -> int:
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
        HostIdentity(args.client_version, args.client_name, "OpenAI interoperability lab", "Python GFDI client"),
        # Static managers used by this reference client: GNCS smart notifications
        # requires config bit 6 and the current-time request path uses bit 71.
        frozenset({6, 71}),
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

    workflow = sub.add_parser("workflow", help="run the independent handshake/auth/time/file workflow on a watch")
    workflow.add_argument("address")
    workflow.add_argument("--pairing-store", default=str(Path.home() / ".local" / "share" / "garmin-proto" / "pairing.json"))
    workflow.add_argument("--client-version", type=int, default=1)
    workflow.add_argument("--client-name", default="Independent Garmin Client")
    workflow.add_argument("--timeout", type=float, default=45.0)
    workflow.add_argument("--battery-percent", type=int, default=100)
    workflow.add_argument("--skip-battery", action="store_true")
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
