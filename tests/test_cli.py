from __future__ import annotations

from garmin_proto_lab.cli import build_parser
from garmin_proto_lab.multilink import DEFAULT_INDEPENDENT_CLIENT_ID


def test_workflow_next_gen_flags_imply_stable_default_client_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["workflow", "00:11:22:33:44:55", "--next-gen-files"])
    assert args.next_gen_files is True
    assert args.multilink_client_id == DEFAULT_INDEPENDENT_CLIENT_ID

    overridden = parser.parse_args([
        "workflow",
        "00:11:22:33:44:55",
        "--download-first-next-gen-fitness",
        "--next-gen-compression",
        "--multilink-client-id",
        "0x1234",
    ])
    assert overridden.download_first_next_gen_fitness is True
    assert overridden.next_gen_compression is True
    assert overridden.multilink_client_id == 0x1234


def test_pair_command_isolated_hardware_probe_options() -> None:
    parser = build_parser()
    default_pair = parser.parse_args(["pair", "00:11:22:33:44:55"])
    assert default_pair.system_bond is False
    assert default_pair.garmin_auth is False
    args = parser.parse_args([
        "pair",
        "00:11:22:33:44:55",
        "--timeout",
        "12",
        "--system-bond",
    ])
    assert args.command == "pair"
    assert args.timeout == 12.0
    assert args.system_bond is True
    assert args.garmin_auth is False


def test_fitness_sync_command_uses_automatic_pairing_bond_policy(tmp_path) -> None:
    parser = build_parser()
    args = parser.parse_args([
        "fitness-sync",
        "00:11:22:33:44:55",
        "--output",
        str(tmp_path),
    ])
    assert args.command == "fitness-sync"
    assert args.system_bond is False
    assert args.compression is False
    assert args.multilink_client_id == DEFAULT_INDEPENDENT_CLIENT_ID


def test_reset_pairing_parser_defaults_to_full_fresh_state() -> None:
    parser = build_parser()
    args = parser.parse_args(["reset-pairing", "00:11:22:33:44:55"])
    assert args.command == "reset-pairing"
    assert args.keep_system_bond is False
    keep = parser.parse_args(["reset-pairing", "00:11:22:33:44:55", "--keep-system-bond"])
    assert keep.keep_system_bond is True


def test_fitness_sync_activity_json_option() -> None:
    parser = build_parser()
    args = parser.parse_args([
        "fitness-sync", "00:11:22:33:44:55", "--output", "/tmp/fit", "--activity-json"
    ])
    assert args.fit_json is True


def test_reset_pairing_removes_system_bond_and_persisted_record(tmp_path, monkeypatch) -> None:
    import asyncio
    import argparse
    from garmin_proto_lab import cli
    from garmin_proto_lab.session import FilePairingStore, PairingRecord

    address = "00:11:22:33:44:55"
    store_path = tmp_path / "pairing.json"
    store = FilePairingStore(store_path)
    store.save(address, PairingRecord(b"L" * 16, b"E" * 2, b"R" * 8))

    removed: list[str] = []

    class Backend:
        async def remove_bond(self, device) -> None:
            removed.append(device.address)

    monkeypatch.setattr(cli, "BleakBackend", lambda: Backend())
    args = argparse.Namespace(
        address=address,
        pairing_store=str(store_path),
        keep_system_bond=False,
    )
    assert asyncio.run(cli._reset_pairing(args)) == 0
    assert removed == [address]
    assert store.load(address) is None


def test_pair_command_can_force_garmin_auth() -> None:
    parser = build_parser()
    args = parser.parse_args(["pair", "00:11:22:33:44:55", "--garmin-auth"])
    assert args.garmin_auth is True
    assert args.system_bond is False



def test_pairing_state_distinguishes_bonded_stub_auth_from_unbonded_garmin_auth(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    import pytest
    from garmin_proto_lab import cli
    from garmin_proto_lab.session import SessionPhase

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(cli.asyncio, "sleep", no_sleep)

    class Backend:
        def __init__(self, bonded: bool) -> None:
            self.bonded = bonded
        async def is_bonded(self) -> bool:
            return self.bonded

    bonded_auth = SimpleNamespace(state=SimpleNamespace(phase=SessionPhase.DISCONNECTED))
    bonded_transport = SimpleNamespace(require_bond=True)
    assert asyncio.run(cli._finish_pairing_state(bonded_auth, bonded_transport, Backend(True), 0.1)) == "system_bond"

    established_auth = SimpleNamespace(state=SimpleNamespace(phase=SessionPhase.ESTABLISHED))
    unbonded_transport = SimpleNamespace(require_bond=False)
    assert asyncio.run(cli._finish_pairing_state(established_auth, unbonded_transport, Backend(False), 0.1)) == "garmin_auth"

    missing_auth = SimpleNamespace(state=SimpleNamespace(phase=SessionPhase.DISCONNECTED))
    with pytest.raises(RuntimeError, match="did not start"):
        asyncio.run(cli._finish_pairing_state(missing_auth, unbonded_transport, Backend(False), 0.1))


def test_multilink_info_command_defaults_to_garmin_client_id() -> None:
    parser = build_parser()
    args = parser.parse_args(["multilink-info", "00:11:22:33:44:55"])
    assert args.command == "multilink-info"
    assert args.multilink_client_id == 1
    assert args.show_identifiers is False
    assert args.system_bond is False
