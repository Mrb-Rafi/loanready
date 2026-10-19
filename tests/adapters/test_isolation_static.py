"""T-ADP-05 (static): sandbox write paths and the write token appear only in adapters/ and
sandbox/; adapters never import sandbox; only adapters/_secrets.py reads the write token."""

from __future__ import annotations

import ast
import re

from infra.integration_check import CODE_DIRS, py_files, static_checks
from infra.paths import REPO_ROOT

WRITE_PATH = re.compile(r"[\"']/(bank/payments/schedule|messaging/send|lender/applications)")


def test_write_paths_only_in_adapters_and_sandbox() -> None:
    """T-ADP-05 proves SBX-A-06, CTL-AC-05: no other package names a sandbox write path."""
    offenders = []
    for f in py_files(REPO_ROOT, CODE_DIRS):
        rel = f.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("adapters/", "sandbox/")):
            continue
        if WRITE_PATH.search(f.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert offenders == []
    assert not [
        p for p in static_checks(REPO_ROOT) if "sandbox write path" in p or "WRITE_TOKEN" in p
    ]


def test_write_token_read_only_in_secrets() -> None:
    """T-ADP-05 proves project rule 3: only adapters/_secrets.py touches the write token."""
    users = [
        f.relative_to(REPO_ROOT).as_posix()
        for f in (REPO_ROOT / "adapters").rglob("*.py")
        if "sandbox_write_token" in f.read_text(encoding="utf-8")
    ]
    assert users == ["adapters/_secrets.py"]


def test_adapters_never_import_sandbox() -> None:
    """ARC-05: adapters reach the sandbox only over HTTP."""
    bad = []
    for f in (REPO_ROOT / "adapters").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            )
            bad += [m for m in mods if m.split(".")[0] in ("sandbox", "governance", "orchestrator")]
    assert bad == []
