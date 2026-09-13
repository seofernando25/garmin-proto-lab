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
    args = parser.parse_args([
        "pair",
        "00:11:22:33:44:55",
        "--timeout",
        "12",
        "--no-bond",
    ])
    assert args.command == "pair"
    assert args.timeout == 12.0
    assert args.no_bond is True
