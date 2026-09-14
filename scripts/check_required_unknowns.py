#!/usr/bin/env python3
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULES = [
    "auth_messages.py",
    "auth_protocol.py",
    "pairing.py",
    "session.py",
    "secure_session.py",
    "transport.py",
    "frame.py",
    "filetransfer.py",
    "file_client.py",
    "file_access_proto.py",
    "file_access_client.py",
    "multilink.py",
    "multilink_client.py",
    "multilink_gfdi.py",
    "mlr.py",
    "fit.py",
    "fitness_sync.py",
]
# Protocol enums legitimately contain names such as UNKNOWN_ITEM. This checker
# only rejects definitions (fields, variables and arguments) still named
# unknown_* in the required pairing/fitness workflow modules.
UNKNOWN_IDENTIFIER = re.compile(r"^unknown(?:_|$)", re.IGNORECASE)
REQUIRED_EVIDENCE = ("S-0021", "S-0022", "S-0023", "S-0024", "S-0025", "S-0027")
REQUIRED_PROTOCOL = ("P-0014", "P-0213", "P-0703", "P-0704", "P-0705", "P-0706")


def scan_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    failures: list[str] = []
    for node in ast.walk(tree):
        definitions: list[tuple[str, int]] = []
        if isinstance(node, ast.arg):
            definitions.append((node.arg, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            definitions.append((node.target.id, node.target.lineno))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    definitions.append((target.id, target.lineno))
        for name, line in definitions:
            if UNKNOWN_IDENTIFIER.match(name) and not name.isupper():
                failures.append(f"{path.relative_to(ROOT)}:{line}: unresolved identifier {name}")
    return failures


def main() -> int:
    failures: list[str] = []
    source = ROOT / "src" / "garmin_proto_lab"
    for name in MODULES:
        path = source / name
        if not path.is_file():
            failures.append(f"missing required module {path.relative_to(ROOT)}")
        else:
            failures.extend(scan_file(path))

    evidence = (ROOT / "evidence" / "static" / "STATIC_MAP.md").read_text(encoding="utf-8")
    protocol = (ROOT / "spec" / "PROTOCOL.md").read_text(encoding="utf-8")
    for marker in REQUIRED_EVIDENCE:
        if marker not in evidence:
            failures.append(f"STATIC_MAP.md missing {marker}")
    for marker in REQUIRED_PROTOCOL:
        if marker not in protocol:
            failures.append(f"PROTOCOL.md missing {marker}")

    if failures:
        print("REQUIRED-WORKFLOW UNKNOWN CHECK FAILED")
        print("\n".join(failures))
        return 1
    print("REQUIRED-WORKFLOW UNKNOWN CHECK PASSED")
    print(f"modules={len(MODULES)} evidence_markers={len(REQUIRED_EVIDENCE)} protocol_markers={len(REQUIRED_PROTOCOL)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
