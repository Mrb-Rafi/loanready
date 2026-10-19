"""PRD/11 §7: seeding loads the sandbox and the books and logs SEED_LOADED."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlmodel import Session, select

from contracts.enums import Decision
from contracts.registry import OPERATING_ACCOUNT_ID
from contracts.schema import RawTransaction
from orchestrator.seed import bundle_sha256
from tests.integration._support import fraud_bill, tiny_with

pytestmark = [pytest.mark.integration]


def test_seed_loads_sandbox_and_logs(make_app: Any) -> None:
    """T-ORC-10 (seed part) proves PRD/11 §7 steps 3-6: MockBank holds the bundle's
    balance, the books know the master data, SEED_LOADED carries counts and bundle hash."""
    bundle = tiny_with()
    app = make_app(bundle)
    summary = app.c.extras["seed"]
    assert summary["counts"]["invoices"] == 1 and summary["counts"]["bills"] == 1
    assert app.ports.bank.get_balance(OPERATING_ACCOUNT_ID) == 271000
    assert app.books.bill("bil_tiny_1") is not None
    rows = [r for r in app.audit_rows() if r.action_type == "SEED_LOADED"]
    assert len(rows) == 1 and rows[0].decision == Decision.EVENT
    assert rows[0].params_masked["bundle_sha256"] == bundle_sha256(bundle)
    assert app.verify_chain() is None


def test_sandbox_opening_line_not_ingested(make_app: Any) -> None:
    """T-ORC-10 (seed part) proves PRD/11 §7 steps 4-5: the starting balance is the OPENING
    entry from the bundle; the sandbox's "opening" bank line is not ingested again, so it
    never reaches the confirm queue."""
    app = make_app(tiny_with())
    txns = app.ports.bank.list_transactions(OPERATING_ACCOUNT_ID, date(2000, 1, 1), app.as_of)
    assert any(t.kind == "opening" for t in txns)
    with Session(app.c.engine) as s:
        refs = {r.source_ref for r in s.exec(select(RawTransaction)).all()}
    assert not any(ref.startswith("txn_opening_") for ref in refs)


def test_sync_counts_flags_logged_by_agent(make_app: Any) -> None:
    """T-ORC-10 (seed part) proves PRD/11 §1: a flag the agent already logged still counts
    in the sync summary, and is logged once."""
    app = make_app(tiny_with([fraud_bill()]))
    flagged = [
        r
        for r in app.audit_rows()
        if r.action_type == "UNTRUSTED_INSTRUCTION_IGNORED"
        and r.params_masked.get("source") in {"BANK", "POS", "BILL"}
    ]
    keys = [(r.params_masked["source"], r.params_masked["source_ref"]) for r in flagged]
    assert keys and len(keys) == len(set(keys))
    assert app.c.extras["seed"]["sync"]["flags"] == len(keys)


def test_seed_cli_on_tiny_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    """PRD/11 §7: ``python -m orchestrator.seed`` resets var/app.db and prints a summary
    (the tiny fixture when no bundle was generated and datagen is absent)."""
    import importlib.util

    if importlib.util.find_spec("sandbox") is None:
        pytest.skip("sandbox (WP2) not present")
    from orchestrator.container import bundle_path
    from orchestrator.seed import main

    if bundle_path().exists():
        pytest.skip("a generated bundle exists in this var dir")
    from sandbox.ratelimit import LIMITER
    from sandbox.settings import reset_settings

    reset_settings()
    LIMITER.reset()
    assert main(["--no-generate"]) == 0
    out = capsys.readouterr().out
    assert "seeded from tiny" in out
    assert "cash (1000+1010)" in out
