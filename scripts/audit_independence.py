#!/usr/bin/env python3
"""Fail-closed audit of the clean-room runtime boundary."""
from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "garmin_proto_lab"
PYPROJECT = ROOT / "pyproject.toml"

FORBIDDEN_TEXT = {
    "analysis/jadx": "runtime references generated JADX evidence",
    "analysis/apktool": "runtime references generated Apktool evidence",
    "/* JADX": "decompiler comment marker in runtime",
    ".smali": "runtime references smali input",
    "classes7.dex": "runtime references DEX layout",
    "com.garmin.android.apps.connectmobile": "runtime references Garmin application class path",
}
FORBIDDEN_IMPORT_ROOTS = {
    "androguard",
    "apktool",
    "jadx",
}
# Obfuscated decompiler class names such as C4565c are evidence-side names and
# should never be needed by the independent runtime.
OBFUSCATED_CLASS = re.compile(r"\bC\d{4,}[A-Za-z0-9_]*\b")


def iter_import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def requirement_name(spec: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", spec)
    return match.group(1).lower() if match else spec.lower()


def main() -> int:
    problems: list[str] = []
    py_files = sorted(SRC.glob("*.py"))
    if not py_files:
        problems.append("no runtime Python files found")

    imports: set[str] = set()
    for path in py_files:
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(ROOT)
        for needle, description in FORBIDDEN_TEXT.items():
            if needle in text:
                problems.append(f"{rel}: {description}: {needle!r}")
        if OBFUSCATED_CLASS.search(text):
            problems.append(f"{rel}: contains evidence-side obfuscated class identifier")
        try:
            tree = ast.parse(text, filename=str(rel))
        except SyntaxError as exc:
            problems.append(f"{rel}: syntax error during audit: {exc}")
            continue
        imports.update(iter_import_roots(tree))

    bad_imports = sorted(imports & FORBIDDEN_IMPORT_ROOTS)
    if bad_imports:
        problems.append("runtime imports decompilation tooling: " + ", ".join(bad_imports))

    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    declared: list[str] = list(config.get("project", {}).get("dependencies", []))
    for values in config.get("project", {}).get("optional-dependencies", {}).values():
        declared.extend(values)
    suspicious = [spec for spec in declared if "garmin" in requirement_name(spec)]
    if suspicious:
        problems.append("runtime dependency appears Garmin-protocol-specific: " + ", ".join(suspicious))

    analysis_imports = sorted(root for root in imports if root in {"analysis", "evidence"})
    if analysis_imports:
        problems.append("runtime imports research/evidence package: " + ", ".join(analysis_imports))

    print(f"runtime files audited: {len(py_files)}")
    print("runtime dependency declarations: " + (", ".join(declared) if declared else "none"))
    print("decompiler/runtime boundary: " + ("FAIL" if problems else "PASS"))
    if problems:
        for problem in problems:
            print("- " + problem)
        return 1
    print("No generated analysis path, decompiler marker/tool import, evidence-side obfuscated class, or Garmin protocol dependency found in runtime source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
