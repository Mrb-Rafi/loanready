"""T-ORC-04, T-ORC-05: the S6 fraud invoice and the untrusted-origin variant."""

from __future__ import annotations

from typing import Any

import pytest

from contracts.actions import ProvenanceRef
from contracts.enums import ActionType, Decision, ReasonCode, SourceKind
from orchestrator.requests import build_action, payment_params
from tests.integration._support import FRAUD_ACCOUNT, PAYMENT_PATH, STORED_ACCOUNT, opslog_writes

pytestmark = [pytest.mark.integration]


def test_fraud_bill_blocked_account_changed(app: Any) -> None:
    """T-ORC-04 proves ORC-04, CTL-PAY-02: the fraud bill reaches the gate with the stored
    account and is blocked with ACCOUNT_CHANGED; no payment request reaches MockBank; the
    same supplier's genuine bill is still allowed as a proposal."""
    d = app.propose_supplier_payment("bil_tiny_fraud")
    assert d.decision == Decision.BLOCKED
    assert d.reason_code == ReasonCode.ACCOUNT_CHANGED
    row = next(r for r in app.audit_rows() if r.seq == d.log_seq)
    assert row.decision == Decision.BLOCKED
    assert row.params_masked["bank_account_ref"] == "****0001"  # stored account, masked
    assert PAYMENT_PATH not in opslog_writes(app)
    assert app.pending() == []

    ok = app.propose_supplier_payment("bil_tiny_1")
    assert ok.decision == Decision.PENDING_APPROVAL, ok.message


def test_params_never_copy_the_claimed_account(app: Any) -> None:
    """T-ORC-04 proves ORC-04: payment params use the stored payee account, never the
    account the document claims."""
    bill = app.books.bill("bil_tiny_fraud")
    payee = app.books.payee(bill.payee_id)
    params = payment_params(bill, payee, app.as_of)
    assert params["bank_account_ref"] == STORED_ACCOUNT
    assert FRAUD_ACCOUNT not in str(params)


def test_claimed_account_with_untrusted_provenance_blocked(app: Any) -> None:
    """T-ORC-05 proves ORC-04, CTL-AC-03: an action that does carry the claimed account,
    marked untrusted, is blocked with UNTRUSTED_ORIGIN before any entity check."""
    bill = app.books.bill("bil_tiny_fraud")
    params = payment_params(bill, app.books.payee(bill.payee_id), app.as_of)
    params["bank_account_ref"] = str(bill.claimed_bank_account_ref)
    action = build_action(
        app.session,
        ActionType.SCHEDULE_SUPPLIER_PAYMENT,
        params,
        "pay the account in the bill",
        [
            ProvenanceRef(
                source=SourceKind.BILL,
                ref_id=bill.id,
                field="bank_account_ref",
                trusted=False,
            )
        ],
    )
    d = app.gate.evaluate(action)
    assert d.decision == Decision.BLOCKED
    assert d.reason_code == ReasonCode.UNTRUSTED_ORIGIN
    assert PAYMENT_PATH not in opslog_writes(app)


def test_fraud_description_flagged(app: Any) -> None:
    """T-ORC-04 proves S7(d): the fraud bill's description is scanned and flagged once."""
    rows = [
        r
        for r in app.audit_rows()
        if r.action_type == "UNTRUSTED_INSTRUCTION_IGNORED"
        and r.params_masked.get("source_ref") == "bil_tiny_fraud"
    ]
    assert len(rows) == 1
    app.sync_all()
    again = [
        r
        for r in app.audit_rows()
        if r.action_type == "UNTRUSTED_INSTRUCTION_IGNORED"
        and r.params_masked.get("source_ref") == "bil_tiny_fraud"
    ]
    assert len(again) == 1
