"""T-ORC-02: scenarios S1-S10 against ``data/expected.json`` (PRD/10 §4, §7), the core
acceptance test of the whole product. Needs every package; skips until assembled."""

from __future__ import annotations

import json
from typing import Any

import pytest

from contracts.enums import ConfirmKind, Decision, Lever, ReasonCode, Scenario
from infra.paths import DATA_DIR
from tests.integration._support import real

pytestmark = [pytest.mark.integration, pytest.mark.real, pytest.mark.slow, real]


@pytest.fixture
def expected() -> dict[str, Any]:
    path = DATA_DIR / "expected.json"
    if not path.exists():
        pytest.skip("data/expected.json missing (run python tasks.py gen)")
    return json.loads(path.read_text(encoding="utf-8"))


def test_s1_personal_expenses(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S1: exactly 3 PERSONAL confirm items; the owner confirms them all."""
    app = seeded_real
    items = [i for i in app.confirm_queue() if i.kind == ConfirmKind.PERSONAL]
    assert len(items) == 3
    assert {i.ref_id for i in items} == set(expected["S1"]["personal_ids"])
    for i in items:
        app.resolve(i.id, "PERSONAL")
    assert not [i for i in app.confirm_queue() if i.kind == ConfirmKind.PERSONAL]


def test_s2_base_negative_week(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S2: the base case first goes negative in week 3."""
    f = seeded_real.forecast()
    assert (
        f.scenarios[Scenario.BASE].first_negative_week == expected["S2"]["first_negative_week_base"]
    )


def test_s3_two_reminders(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S3: exactly the two overdue catering invoices get reminders (S9 excluded)."""
    drafts = seeded_real.draft_reminders()
    assert sorted(d.related_id for d in drafts) == sorted(expected["S3"]["overdue_invoice_ids"])


def test_s4_plan_and_financing(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S4: COLLECT, DELAY and BORROW steps; supplier credit cheapest; MCA dearest."""
    app = seeded_real
    plan = app.cash_plan()
    assert [s.lever for s in plan.steps][:3] == [Lever.COLLECT, Lever.DELAY, Lever.BORROW]
    assert plan.financing_request is not None
    comp = app.financing()
    by_id = {o.product_id: o for o in comp.options}
    assert by_id[comp.cheapest_id].kind.value == expected["S4"]["cheapest_kind"]
    assert comp.most_expensive_id == expected["S4"]["most_expensive_id"]


def test_s5_duplicate(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S5: the second print-shop charge is a DUPLICATE confirm item and logged."""
    app = seeded_real
    dups = [i for i in app.confirm_queue() if i.kind == ConfirmKind.DUPLICATE]
    assert dups and {i.ref_id for i in dups} <= set(expected["S5"]["duplicate_ids"])
    assert any(r.action_type == "DUPLICATE_FLAGGED" for r in app.audit_rows())


def test_s6_fraud_blocked(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S6: payment for the fraud bill is blocked with ACCOUNT_CHANGED and the bill
    is excluded from the forecast inputs."""
    app = seeded_real
    bill_id = expected["S6"]["bill_id"]
    d = app.propose_supplier_payment(bill_id)
    assert d.decision == Decision.BLOCKED
    assert d.reason_code == ReasonCode(expected["S6"]["expected_reason"])
    assert bill_id in {b.id for b in app.excluded_bills()}


def test_s7_injection(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S7: no action from injected text; at least min_flags events logged."""
    app = seeded_real
    flags = [r for r in app.audit_rows() if r.action_type == "UNTRUSTED_INSTRUCTION_IGNORED"]
    assert len(flags) >= expected["S7"]["min_flags"]
    assert app.pending() == []


def test_s8_blurry_receipt(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S8: the blurry receipt needs confirmation."""
    receipts = {r["file"]: r["status"] for r in seeded_real.c.extras["seed"]["receipts"]}
    assert receipts[expected["S8"]["receipt"]] == expected["S8"]["status"]


def test_s9_impaired_receivable(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S9: the 75-day invoice sits in 61-90 and gets no reminder."""
    app = seeded_real
    row = next(r for r in app.receivables().rows if r.invoice_id == expected["S9"]["invoice_id"])
    assert row.bucket == expected["S9"]["bucket"]
    assert expected["S9"]["invoice_id"] not in {d.related_id for d in app.draft_reminders()}


def test_s10_legitimate_payment(seeded_real: Any, expected: dict[str, Any]) -> None:
    """T-ORC-02 S10: proposal -> approval -> EXECUTED with a MockBank sbx_ref."""
    app = seeded_real
    d = app.propose_supplier_payment(expected["S10"]["bill_id"])
    assert d.decision == Decision.PENDING_APPROVAL, d.message
    assert (
        app.gate.proposal(d.proposal_id).action.params["amount_cents"]
        == expected["S10"]["amount_cents"]
    )
    done = app.approve_and_execute(d.proposal_id)
    assert done.decision == Decision.EXECUTED and done.result.sbx_ref
