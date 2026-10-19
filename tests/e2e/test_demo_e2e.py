"""T-E2E-01/02 (PRD/13 §5, PRD/15 §4.10): the headless Rina story end to end, evidence export
and determinism. Needs every package; skips cleanly until all hand-offs are assembled."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration._support import all_packages_present

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.real,
    pytest.mark.skipif(not all_packages_present(), reason="needs every package assembled"),
]

CSV_COLUMNS = [
    "seq", "ts", "action_id", "action_type", "tier", "origin", "principal_id", "decision",
    "reason_code", "approved_by", "approved_at", "params_masked", "result_masked", "sbx_ref",
    "control_ids", "prev_hash", "row_hash",
]  # fmt: skip


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One headless demo into a temp evidence folder (never the repo's evidence/)."""
    from scripts import run_demo

    out = tmp_path_factory.mktemp("evidence_a")
    rc = run_demo.main(["--out-dir", str(out)])
    summary = json.loads((out / "demo_summary.json").read_text(encoding="utf-8"))
    return {"rc": rc, "out": out, "summary": summary}


def _csv_rows(path: Path) -> list[dict[str, str]]:
    csv.field_size_limit(2**31 - 1)
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_demo_all_scenarios_true(demo_run: dict[str, Any]) -> None:
    """T-E2E-01 proves PRD/13 §1, PRD/00 §4: every S1-S10 flag true, books reconciled, pack
    submitted, chain OK, exit code 0."""
    s = demo_run["summary"]
    assert demo_run["rc"] == 0, s["steps"]
    assert set(s["scenarios"]) == {f"S{i}" for i in range(1, 11)}
    assert all(s["scenarios"].values()), s["scenarios"]
    assert s["chain_ok"] and s["books_reconciled"] and s["loan_pack_submitted"]
    assert s["headline"]["first_negative_week_base"] == 3
    assert "Merchant cash advance" in s["headline"]["most_expensive"]


def test_demo_csv_columns_and_decisions(demo_run: dict[str, Any]) -> None:
    """T-E2E-01 proves CTL-LOG-01..03: the exported CSV has the expected columns and at
    least one EXECUTED, BLOCKED, APPROVED, EVENT and PENDING_APPROVAL row; the counts in the
    summary match the CSV; approvals are recorded for the owner."""
    path = demo_run["out"] / "action_log.csv"
    with path.open(encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == CSV_COLUMNS
    rows = _csv_rows(path)
    decisions = {r["decision"] for r in rows}
    assert {"EXECUTED", "BLOCKED", "APPROVED", "EVENT", "PENDING_APPROVAL"} <= decisions
    approved = [r for r in rows if r["decision"] == "APPROVED"]
    assert approved and all(r["approved_by"] == "owner-rina" for r in approved)
    counts = demo_run["summary"]["counts"]
    for d in decisions:
        # the summary is taken after the export, so it also counts EXPORT_CREATED
        assert counts[d] >= sum(r["decision"] == d for r in rows)


def test_export_evidence_files(demo_run: dict[str, Any], tmp_path: Path) -> None:
    """T-E2E-01 proves PRD/13 §1, CTL-LOG-03, CTL-LOG-05: evidence export writes the action
    log, the sandbox ops log and both chain verifications, all intact."""
    from scripts import export_evidence

    rc = export_evidence.main(["--out-dir", str(tmp_path), "--no-tests"])
    assert rc == 0
    for name in (
        "action_log.csv",
        "sandbox_ops.csv",
        "chain_verification.txt",
        "sandbox_chain_verification.txt",
        "README.md",
    ):
        assert (tmp_path / name).exists(), name
    assert "chain: OK" in (tmp_path / "chain_verification.txt").read_text(encoding="utf-8")
    sandbox = (tmp_path / "sandbox_chain_verification.txt").read_text(encoding="utf-8")
    assert "BROKEN" not in sandbox and "bank: OK" in sandbox
    assert _csv_rows(tmp_path / "sandbox_ops.csv")


@pytest.mark.slow
def test_demo_evidence_deterministic(
    demo_run: dict[str, Any], tmp_path_factory: pytest.TempPathFactory
) -> None:
    """T-E2E-02 proves PRD-NF-02: a second run gives a byte-identical action log CSV and
    demo summary."""
    from scripts import run_demo

    out = tmp_path_factory.mktemp("evidence_b")
    assert run_demo.main(["--out-dir", str(out)]) == 0
    a, b = demo_run["out"], out
    assert (a / "action_log.csv").read_bytes() == (b / "action_log.csv").read_bytes()
    assert (a / "demo_summary.json").read_bytes() == (b / "demo_summary.json").read_bytes()
