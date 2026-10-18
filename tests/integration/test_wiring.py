"""T-ORC-01 wiring: ``build_app`` composes every service and every AppFacade method returns
the right DTO type, with fakes for missing packages (ORC-01) and with the real WP1/WP2."""

from __future__ import annotations

import inspect
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from contracts.actions import AuditRecord, GateDecision, Principal, Proposal
from contracts.dto_app import CapUsage, ChatReply, ControlRow, DashboardDTO, TierRow
from contracts.dto_books import ConfirmItemDTO, ReceivablesAgeingDTO
from contracts.dto_financing import FinancingComparisonDTO, ReadinessDTO
from contracts.dto_outputs import LoanPackDTO, MessageDraftDTO
from contracts.dto_planning import CashPlanDTO, ForecastDTO
from contracts.enums import ActionType, Decision, ReasonCode, Role, Scenario
from contracts.errors import PermissionDenied
from contracts.services import AppFacade
from tests.integration._support import needs_sandbox, real

pytestmark = pytest.mark.integration


def test_app_implements_every_facade_method() -> None:
    """T-ORC-01 proves PRD/11 §3: App defines every AppFacade method with the same params."""
    from orchestrator.facade import App

    for name, member in inspect.getmembers(AppFacade, inspect.isfunction):
        if name.startswith("_"):
            continue
        assert hasattr(App, name), f"App lacks {name}"
        want = list(inspect.signature(member).parameters)
        got = list(inspect.signature(getattr(App, name)).parameters)
        assert got == want, f"{name}: {got} != {want}"


def test_fakes_true_runs_everything_in_memory(make_app: Any) -> None:
    """T-ORC-01 proves ORC-01: fakes=True wires fakes for every composed package."""
    app = make_app(fakes=True, seed=False)
    assert isinstance(app, AppFacade)
    assert set(app.c.modes.values()) == {"fake"}
    assert app.health()["fake"] is True


def test_fakes_auto_uses_real_governance_and_adapters(make_app: Any) -> None:
    """T-ORC-01 proves ORC-01: assembled packages are real, missing ones are fakes."""
    from orchestrator.container import package_present

    app = make_app()
    for pkg, mode in app.c.modes.items():
        assert mode == ("real" if package_present(pkg) else "fake"), pkg
    assert app.c.modes["governance"] == "real"
    assert app.c.modes["adapters"] == "real"
    assert app.health()["sandbox"].get("status") != "down"


def test_fakes_false_names_missing_packages(settings: Any, tmp_path: Path) -> None:
    """T-ORC-01 proves ORC-01: fakes=False refuses a partly assembled tree, naming packages."""
    from infra.db import engine_for
    from orchestrator.container import COMPOSED, build_app, package_present

    missing = [p for p in COMPOSED if not package_present(p)]
    if not missing:
        pytest.skip("every package present")
    with pytest.raises(RuntimeError, match=missing[0]):
        build_app(settings, fakes=False, engine=engine_for(tmp_path / "app.db"))


def test_version_mismatch_stops_start_up(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-ORC-01 proves ORC-02: a contracts major mismatch stops build_app."""
    import governance
    from infra.db import engine_for
    from orchestrator.container import build_app

    monkeypatch.setattr(governance, "REQUIRES_CONTRACTS", "99")
    with pytest.raises(RuntimeError, match="governance"):
        build_app(settings, engine=engine_for(tmp_path / "app.db"))


@needs_sandbox
def test_every_method_returns_its_dto(make_app: Any, tmp_path: Path) -> None:
    """T-ORC-01 proves PRD/11 §3: each facade method returns its contracts type."""
    app = make_app()
    assert isinstance(app.health(), dict)
    assert isinstance(app.dashboard(), DashboardDTO)
    assert isinstance(app.sync_all(), dict)
    assert all(isinstance(i, ConfirmItemDTO) for i in app.confirm_queue())
    view = app.books_view()
    assert {"journal", "pnl", "cash_flow", "balance_sheet", "reconciliation"} <= set(view)
    assert isinstance(app.receivables(), ReceivablesAgeingDTO)
    f = app.forecast()
    assert isinstance(f, ForecastDTO) and Scenario.BASE in f.scenarios
    assert isinstance(app.forecast_explanation(), str)
    assert isinstance(app.cash_plan(), CashPlanDTO)
    drafts = app.draft_reminders()
    assert drafts and all(isinstance(d, MessageDraftDTO) for d in drafts)
    assert all(isinstance(d, MessageDraftDTO) for d in app.draft_delay_requests())
    assert isinstance(app.propose_send(drafts[0].draft_id), GateDecision)
    assert all(isinstance(p, Proposal) for p in app.pending())
    assert isinstance(app.financing(), FinancingComparisonDTO)
    assert isinstance(app.financing_explanation(), str)
    assert isinstance(app.readiness(), ReadinessDTO)
    pack = app.build_loan_pack(100000, 90, "Working capital")
    assert isinstance(pack, LoanPackDTO)
    assert isinstance(app.propose_submit_pack(pack.pack_id), GateDecision)
    assert isinstance(app.chat("help"), ChatReply)
    blocked = app.try_blocked(ActionType.ACCEPT_LOAN, {"offer_id": "x"})
    assert blocked.decision == Decision.BLOCKED and blocked.reason_code == ReasonCode.BLOCKED_TYPE
    assert all(isinstance(r, AuditRecord) for r in app.audit_rows(5))
    assert app.verify_chain() is None
    assert app.export_csv(tmp_path / "audit.csv").exists()
    assert isinstance(app.audit_counts(), dict)
    assert isinstance(app.cap_usage(), CapUsage)
    assert app.settings()["kill_switch"] == "false"
    assert all(isinstance(r, TierRow) for r in app.tier_table())
    assert all(isinstance(r, ControlRow) for r in app.controls_table())
    assert isinstance(app.principal(), Principal)
    assert isinstance(app.data_version(), int)


@needs_sandbox
def test_principal_switch_and_owner_only_settings(make_app: Any) -> None:
    """T-ORC-01 proves PRD/11 §3: set_principal switches roles; settings are owner only;
    data_version increases on writes (UI-12)."""
    app = make_app()
    v0 = app.data_version()
    app.set_principal(Role.BOOKKEEPER)
    assert app.principal().role == Role.BOOKKEEPER
    assert app.data_version() > v0
    with pytest.raises(PermissionDenied):
        app.set_kill_switch(True)
    with pytest.raises(PermissionDenied):
        app.update_setting("cap_per_action_cents", "1000")
    with pytest.raises(ValueError):
        app.set_principal(Role.AGENT)
    app.set_principal(Role.OWNER)
    app.update_setting("cap_per_action_cents", "400000")
    assert app.settings()["cap_per_action_cents"] == "400000"


@needs_sandbox
def test_forecast_excludes_suspected_fraud_bill(app: Any) -> None:
    """T-ORC-02 (S6 part) proves ORC-07: a bill claiming a different account is left out of
    the forecast inputs and listed as excluded pending verification."""
    excluded = app.excluded_bills()
    assert [b.id for b in excluded] == ["bil_tiny_fraud"]
    inputs = app._assembly().inputs
    assert "bil_tiny_fraud" not in {b.bill_id for b in inputs.open_bills}
    assert "bil_tiny_1" in {b.bill_id for b in inputs.open_bills}


@real
def test_fakes_false_with_every_package(settings: Any, tmp_path: Path) -> None:
    """T-ORC-01 proves ORC-01: with every WP assembled, fakes=False builds."""
    from infra.db import engine_for
    from orchestrator.container import build_app

    app = build_app(settings, fakes=False, engine=engine_for(tmp_path / "app.db"))
    try:
        assert set(app.c.modes.values()) == {"real"}
        assert app.as_of == date(2026, 10, 14)
    finally:
        app.close()
