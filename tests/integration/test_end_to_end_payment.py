"""T-ORC-03: supplier payment end to end (S10 on the tiny fixture: BILL-1 to Tiny Mills)."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.enums import ActionType, Decision, ProposalStatus, ReasonCode, Role
from contracts.errors import LoanReadyError
from tests.integration._support import PAYMENT_PATH, STORED_ACCOUNT, balance, opslog_writes

pytestmark = [pytest.mark.integration]


def test_propose_approve_execute_payment(app: Any) -> None:
    """T-ORC-03 proves PRD/11 §4, ORC-03, ORC-04: propose -> approve -> execute; MockBank
    shows the payment; PENDING_APPROVAL/APPROVED/EXECUTED rows share the action_id; the
    chain stays intact."""
    before = balance(app)
    d = app.propose_supplier_payment("bil_tiny_1")
    assert d.decision == Decision.PENDING_APPROVAL, d.message
    prop = app.gate.proposal(d.proposal_id)
    assert prop.action.params["bank_account_ref"] == STORED_ACCOUNT
    assert prop.action.params["amount_cents"] == 80000
    assert prop.action.on_behalf_of.role == Role.OWNER
    assert prop.summary.payee_account_masked == "****0001"

    done = app.approve_and_execute(d.proposal_id)
    assert done.decision == Decision.EXECUTED, done.message
    assert done.result is not None and done.result.sbx_ref
    pay = app.ports.bank.get_payment(done.result.sbx_ref)
    assert pay.amount_cents == 80000 and str(pay.status) == "settled"
    assert balance(app) == before - 80000
    assert opslog_writes(app).count(PAYMENT_PATH) == 1

    rows = [r for r in app.audit_rows() if r.action_id == d.action_id]
    assert [r.decision for r in rows] == [
        Decision.PENDING_APPROVAL,
        Decision.APPROVED,
        Decision.EXECUTED,
    ]
    assert rows[-1].sbx_ref == done.result.sbx_ref
    assert app.gate.proposal(d.proposal_id).status == ProposalStatus.EXECUTED
    assert app.verify_chain() is None


def test_execute_without_approval_is_blocked(app: Any) -> None:
    """T-ORC-03 proves CTL-AU-01: execute with no session token is blocked and nothing is
    sent to MockBank."""
    d = app.propose_supplier_payment("bil_tiny_1")
    out = app.execute(d.proposal_id)
    assert out.decision == Decision.BLOCKED
    assert out.reason_code == ReasonCode.APPROVAL_REQUIRED
    assert PAYMENT_PATH not in opslog_writes(app)


def test_bookkeeper_cannot_approve_payment(app: Any) -> None:
    """T-ORC-03 proves CTL-SD-01: only the owner approves payments."""
    d = app.propose_supplier_payment("bil_tiny_1")
    app.set_principal(Role.BOOKKEEPER)
    with pytest.raises(LoanReadyError) as exc:
        app.approve(d.proposal_id)
    assert exc.value.reason_code == ReasonCode.ROLE_NOT_ALLOWED
    assert app.pending()[0].proposal_id == d.proposal_id


def test_reject_leaves_bank_untouched(app: Any) -> None:
    """T-ORC-03 proves PRD/11 §3: a rejected proposal is never executed."""
    before = balance(app)
    d = app.propose_supplier_payment("bil_tiny_1")
    r = app.reject(d.proposal_id, "not now")
    assert r.decision == Decision.REJECTED
    assert app.pending() == []
    assert balance(app) == before


def test_cancel_future_payment(app: Any) -> None:
    """T-ORC-03 proves PRD/11 §3 cancel_payment (P1): a future-dated payment can be undone
    through a second T2 proposal bound to the original action."""
    from datetime import date

    d = app.propose_supplier_payment("bil_tiny_1", value_date=date(2026, 10, 19))
    done = app.approve_and_execute(d.proposal_id)
    assert done.decision == Decision.EXECUTED, done.message
    ref = done.result.sbx_ref
    c = app.cancel_payment(ref)
    assert c.decision == Decision.PENDING_APPROVAL, c.message
    prop = app.gate.proposal(c.proposal_id)
    assert prop.action.type == ActionType.CANCEL_PAYMENT
    assert prop.action.params["original_action_id"] == d.action_id
    undone = app.approve_and_execute(c.proposal_id)
    assert undone.decision == Decision.EXECUTED, undone.message
    assert str(app.ports.bank.get_payment(ref).status) == "cancelled"
