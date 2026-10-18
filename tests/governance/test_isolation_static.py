"""T-GOV-20 (static): no UPDATE/DELETE on action_log; governance imports only contracts and
infra; the governance service satisfies the contracts protocols."""

from __future__ import annotations

import ast
import re
from typing import Any

from contracts.services import AuditLogService, ControlsRegistry, GateService
from infra.paths import REPO_ROOT

GOV = REPO_ROOT / "governance"


def test_no_update_or_delete_sql_on_action_log() -> None:
    """T-GOV-20 proves GOV-12, CTL-LOG-01: no code in governance/ updates or deletes log rows."""
    pattern = re.compile(r"(UPDATE\s+action_log|DELETE\s+FROM\s+action_log)", re.IGNORECASE)
    offenders = [f.name for f in GOV.rglob("*.py") if pattern.search(f.read_text(encoding="utf-8"))]
    assert offenders == []


def test_governance_imports_only_lower_layers() -> None:
    """ARC-04: governance never imports another Layer 2 package."""
    allowed = {"contracts", "infra", "governance"}
    first_party = allowed | {
        "books",
        "planning",
        "financing",
        "outputs",
        "agent",
        "adapters",
        "datagen",
        "orchestrator",
        "ui",
        "scripts",
        "sandbox",
    }
    bad = []
    for f in GOV.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            )
            bad += [f"{f.name}: {n}" for n in names if n.split(".")[0] in first_party - allowed]
    assert bad == []


def test_protocols_satisfied(gov: Any) -> None:
    """CON-20: Gate, AuditLog and Controls implement the contracts protocols."""
    assert isinstance(gov.gate, GateService)
    assert isinstance(gov.audit, AuditLogService)
    assert isinstance(gov.controls, ControlsRegistry)
