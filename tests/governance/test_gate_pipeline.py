"""T-GOV-04..09, T-GOV-16: the gate pipeline steps, entity checks and the Tier 3 path."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from contracts.actions import AGENT, BOOKKEEPER, OWNER, SYSTEM, ProvenanceRef
from contracts.enums import ActionType, Decision, Origin, ReasonCode, SourceKind, Tier
from contracts.testing.builders import make_invoice


def blocked(gov: Any, action: Any, reason: ReasonCode) -> None:
    d = gov.gate.evaluate(action)
    assert d.decision == Decision.BLOCKED, d
    assert d.reason_code == reason, d
    row = gov.audit.rows()[-1]
    assert row.seq == d.log_seq and row.decision == Decision.BLOCKED and row.reason_code == reason
    assert "CTL-AC-01" in row.control_ids and "CTL-LOG-02" in row.control_ids


def test_tier0_and_tier1(gov: Any) -> None:
    """CTL-AC-01: Tier 0 -> ALLOWED, Tier 1 -> DRAFTED, each with one log row."""
    d0 = gov.gate.evaluate(gov.act(ActionType.RUN_FORECAST, {"as_of": "2026-10-14"}))
    assert d0.decision == Decision.ALLOWED and d0.tier == Tier.T0_OBSERVE
    d1 = gov.gate.evaluate(gov.act(ActionType.DRAFT_REMINDER, {"invoice_id": "inv_tiny_1"}))
    assert d1.decision == Decision.DRAFTED
    d1b = gov.gate.evaluate(
        gov.act(
            ActionType.DRAFT_LOAN_PACK,
            {"requested_amount_cents": 70000, "term_days": 30, "purpose": "Working capital"},
        )
    )
    assert d1b.decision == Decision.DRAFTED
    assert [r.decision for r in gov.audit.rows()] == [
        Decision.ALLOWED,
        Decision.DRAFTED,
        Decision.DRAFTED,
    ]


def test_untrusted_origin_and_provenance(gov: Any) -> None:
    """T-GOV-04 proves CTL-AC-03, PRD-21: origin UNTRUSTED_DATA blocked; untrusted provenance
    on payee/amount/account/to_ref blocked; SCHEDULE and SYSTEM origins limited."""
    blocked(
        gov,
        gov.act(
            ActionType.SCHEDULE_SUPPLIER_PAYMENT, gov.pay().params, origin=Origin.UNTRUSTED_DATA
        ),
        ReasonCode.UNTRUSTED_ORIGIN,
    )
    for field in ("payee_id", "amount_cents", "bank_account_ref", "value_date"):
        prov = [
            ProvenanceRef(source=SourceKind.BILL, ref_id="bil_fraud", field=field, trusted=False)
        ]
        blocked(
            gov,
            gov.act(ActionType.SCHEDULE_SUPPLIER_PAYMENT, gov.pay().params, provenance=prov),
            ReasonCode.UNTRUSTED_ORIGIN,
        )
    prov = [ProvenanceRef(source=SourceKind.EMAIL, ref_id="x", field="to_ref", trusted=False)]
    blocked(
        gov,
        gov.act(ActionType.SEND_REMINDER, gov.reminder().params, provenance=prov),
        ReasonCode.UNTRUSTED_ORIGIN,
    )
    trusted = [
        ProvenanceRef(
            source=SourceKind.SYSTEM, ref_id="bil_tiny_1", field="amount_cents", trusted=True
        ),
        ProvenanceRef(
            source=SourceKind.BILL, ref_id="bil_tiny_1", field="description", trusted=False
        ),
    ]
    ok = gov.gate.evaluate(
        gov.act(ActionType.SCHEDULE_SUPPLIER_PAYMENT, gov.pay().params, provenance=trusted)
    )
    assert ok.decision == Decision.PENDING_APPROVAL
    blocked(
        gov,
        gov.act(ActionType.SEND_REMINDER, gov.reminder().params, origin=Origin.SCHEDULE),
        ReasonCode.UNTRUSTED_ORIGIN,
    )
    blocked(
        gov,
        gov.act(
            ActionType.DRAFT_REMINDER,
            {"invoice_id": "inv_tiny_1"},
            origin=Origin.SYSTEM,
            by=SYSTEM,
            on_behalf_of=None,
        ),
        ReasonCode.UNTRUSTED_ORIGIN,
    )
    assert (
        gov.gate.evaluate(
            gov.act(
                ActionType.RECONCILE,
                {"as_of": "2026-10-14"},
                origin=Origin.SYSTEM,
                by=SYSTEM,
                on_behalf_of=None,
            )
        ).decision
        == Decision.ALLOWED
    )


def test_role_rules_for_requests(gov: Any) -> None:
    """T-GOV-05 proves CTL-AC-04: system cannot create Tier 2; the agent needs a human
    on whose behalf it acts."""
    blocked(
        gov,
        gov.act(
            ActionType.SCHEDULE_SUPPLIER_PAYMENT, gov.pay().params, by=SYSTEM, on_behalf_of=None
        ),
        ReasonCode.ROLE_NOT_ALLOWED,
    )
    blocked(
        gov,
        gov.act(
            ActionType.SCHEDULE_SUPPLIER_PAYMENT, gov.pay().params, by=AGENT, on_behalf_of=None
        ),
        ReasonCode.ROLE_NOT_ALLOWED,
    )
    blocked(
        gov,
        gov.act(ActionType.RUN_FORECAST, {"as_of": "2026-10-14"}, by=AGENT, on_behalf_of=SYSTEM),
        ReasonCode.ROLE_NOT_ALLOWED,
    )
    assert (
        gov.gate.evaluate(
            gov.act(
                ActionType.SCHEDULE_SUPPLIER_PAYMENT,
                gov.pay().params,
                by=BOOKKEEPER,
                on_behalf_of=None,
            )
        ).decision
        == Decision.PENDING_APPROVAL
    )


@pytest.mark.parametrize(
    "change",
    [
        {"amount_cents": -5},
        {"amount_cents": 0},
        {"value_date": "2026-10-13"},
        {"extra_field": 1},
        {"value_date": "not-a-date"},
    ],
)
def test_invalid_params(gov: Any, change: dict[str, Any]) -> None:
    """T-GOV-06 proves CTL-AC-01: negative/zero amount, past value date, extra field ->
    INVALID_PARAMS."""
    blocked(gov, gov.pay(**change), ReasonCode.INVALID_PARAMS)


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"bill_id": "bil_missing"}, ReasonCode.UNKNOWN_ENTITY),
        ({"payee_id": "pye_missing"}, ReasonCode.UNKNOWN_ENTITY),
        (
            {
                "bill_id": "bil_blk",
                "payee_id": "pye_blocked",
                "bank_account_ref": "SBX-BS-00000003",
            },
            ReasonCode.PAYEE_BLOCKED,
        ),
        (
            {"bill_id": "bil_new", "payee_id": "pye_new", "bank_account_ref": "SBX-NS-00000002"},
            ReasonCode.NEW_PAYEE,
        ),
        ({"bank_account_ref": "SBX-TM-77100938"}, ReasonCode.ACCOUNT_CHANGED),
        ({"bill_id": "bil_fraud"}, ReasonCode.ACCOUNT_CHANGED),
        ({"bill_id": "bil_paid"}, ReasonCode.UNKNOWN_ENTITY),
        ({"amount_cents": 80_001}, ReasonCode.AMOUNT_EXCEEDS_OUTSTANDING),
    ],
)
def test_payment_entity_checks(gov: Any, kw: dict[str, Any], reason: ReasonCode) -> None:
    """T-GOV-07 proves CTL-PAY-01, CTL-PAY-02, CTL-PAY-03: unknown bill/payee, blocked payee,
    new payee, account changed in params, account changed on the bill (fraud invoice S6),
    payee of another bill, amount above outstanding."""
    blocked(gov, gov.pay(**kw), reason)


def test_fraud_invoice_logs_pay02(gov: Any) -> None:
    """T-GOV-07 proves CTL-PAY-02, REG-PAY-02/03: the S6-style bill is blocked and the masked
    claimed account never reaches the log in clear."""
    blocked(gov, gov.pay(bill_id="bil_fraud"), ReasonCode.ACCOUNT_CHANGED)
    row = gov.audit.rows()[-1]
    assert "CTL-PAY-02" in row.control_ids
    assert "77100938" not in row.model_dump_json()


def test_payment_closed_period(gov: Any) -> None:
    """T-GOV-07 proves CTL-FR-03: value date in a closed period -> PERIOD_LOCKED."""
    gov.lookup.closed_months.add("2026-10")
    blocked(gov, gov.pay(), ReasonCode.PERIOD_LOCKED)


def test_paid_before_payee_is_known(gov: Any) -> None:
    """CTL-PAY-01: a payee paid before (not preregistered) is known."""
    d = gov.gate.evaluate(
        gov.pay(bill_id="bil_paid", payee_id="pye_paid", bank_account_ref="SBX-PB-00000004")
    )
    assert d.decision == Decision.PENDING_APPROVAL
    assert "First payment to this payee in 30 days" not in d.summary.warnings


@pytest.mark.parametrize(
    "kw,reason",
    [
        ({"invoice_id": "inv_future"}, ReasonCode.NOT_OVERDUE),
        ({"invoice_id": "inv_missing"}, ReasonCode.UNKNOWN_ENTITY),
        ({"draft_id": "drf_missing"}, ReasonCode.DRAFT_MISMATCH),
        ({"body_sha256": "0" * 64}, ReasonCode.DRAFT_MISMATCH),
        ({"amount_cents": 59_999}, ReasonCode.AMOUNT_MISMATCH),
        ({"to_ref": "someone@else.test"}, ReasonCode.UNKNOWN_ENTITY),
    ],
)
def test_reminder_checks(gov: Any, kw: dict[str, Any], reason: ReasonCode) -> None:
    """T-GOV-08 proves CTL-AI-01: not overdue, draft mismatch, body hash mismatch, amount
    mismatch, wrong recipient."""
    blocked(gov, gov.reminder(**kw), reason)


def test_reminder_draft_must_be_unsent(gov: Any) -> None:
    """T-GOV-08: a draft already sent cannot be proposed again."""
    sent = gov.lookup.draft("drf_tiny_1").model_copy(update={"status": "sent"})
    gov.lookup.drafts = [sent]
    blocked(gov, gov.reminder(), ReasonCode.DRAFT_MISMATCH)


def test_delay_request_checks(gov: Any) -> None:
    """GOV §3.3: delay only penalty-free, non-essential bills within grace; draft and
    recipient checks; drafts the same."""
    assert gov.gate.evaluate(gov.delay()).decision == Decision.PENDING_APPROVAL
    blocked(gov, gov.delay(proposed_date="2026-11-30"), ReasonCode.INVALID_PARAMS)
    blocked(gov, gov.delay(bill_id="bil_tiny_1"), ReasonCode.INVALID_PARAMS)
    blocked(gov, gov.delay(to_ref="other@x.test"), ReasonCode.UNKNOWN_ENTITY)
    blocked(gov, gov.delay(bill_id="bil_missing"), ReasonCode.UNKNOWN_ENTITY)
    ok = gov.gate.evaluate(
        gov.act(
            ActionType.DRAFT_DELAY_REQUEST, {"bill_id": "bil_delay", "proposed_date": "2026-11-10"}
        )
    )
    assert ok.decision == Decision.DRAFTED
    blocked(
        gov,
        gov.act(
            ActionType.DRAFT_DELAY_REQUEST, {"bill_id": "bil_delay", "proposed_date": "2026-12-01"}
        ),
        ReasonCode.INVALID_PARAMS,
    )
    blocked(
        gov,
        gov.act(ActionType.DRAFT_REMINDER, {"invoice_id": "inv_future"}),
        ReasonCode.NOT_OVERDUE,
    )


def test_loan_pack_checks(gov: Any) -> None:
    """T-GOV-09 proves CTL-FIN-04: unknown pack, hash mismatch, tampered file, unknown lender."""
    assert gov.gate.evaluate(gov.submit()).decision == Decision.PENDING_APPROVAL
    blocked(gov, gov.submit(pack_id="pck_missing"), ReasonCode.UNKNOWN_ENTITY)
    blocked(gov, gov.submit(pack_sha256="a" * 64), ReasonCode.PACK_HASH_MISMATCH)
    blocked(gov, gov.submit(lender_id="lender_other"), ReasonCode.UNKNOWN_ENTITY)
    Path(gov.lookup.loan_pack("pck_tiny_1").path).write_bytes(b"%PDF-1.4 tampered\n")
    blocked(gov, gov.submit(), ReasonCode.PACK_HASH_MISMATCH)


def test_entities_rechecked_at_execution(gov: Any) -> None:
    """GOV §3.2 step 4: a bill paid between approval and execution is refused."""
    d = gov.gate.evaluate(gov.pay())
    token = gov.gate.approve(d.proposal_id, OWNER)
    paid = gov.lookup.bill("bil_tiny_1").model_copy(update={"paid_cents": 80_000, "status": "PAID"})
    gov.lookup.bills = [paid if b.id == "bil_tiny_1" else b for b in gov.lookup.bills]
    e = gov.gate.execute(d.proposal_id, token.token_id, OWNER)
    assert e.reason_code == ReasonCode.UNKNOWN_ENTITY and gov.executor.calls == []


def test_executor_failure_logged(gov: Any) -> None:
    """GOV §3.2 step 8: ExecutionResult(ok=False) -> FAILED with its error code; never retried."""
    gov.executor.fail = True
    d = gov.gate.evaluate(gov.pay())
    e = gov.approve_execute(d)
    assert e.decision == Decision.FAILED and e.reason_code == ReasonCode.SANDBOX_ERROR
    assert len(gov.executor.calls) == 1
    assert gov.audit.rows()[-1].decision == Decision.FAILED

    from contracts.errors import SandboxError

    def raise_sandbox(action: Any, ticket: Any) -> Any:
        raise SandboxError("short", reason_code=ReasonCode.INSUFFICIENT_FUNDS, http_status=409)

    gov.executor.execute = raise_sandbox  # type: ignore[method-assign]
    d2 = gov.gate.evaluate(
        gov.pay(bill_id="bil_paid", payee_id="pye_paid", bank_account_ref="SBX-PB-00000004")
    )
    e2 = gov.approve_execute(d2)
    assert e2.decision == Decision.FAILED and e2.reason_code == ReasonCode.INSUFFICIENT_FUNDS


def test_cancel_payment(gov: Any) -> None:
    """CTL-PAY-05 (SHOULD): cancel only an executed payment with a future value date."""
    future = gov.gate.evaluate(gov.pay(value_date="2026-10-20"))
    done = gov.approve_execute(future)
    cancel = gov.act(
        ActionType.CANCEL_PAYMENT,
        {"sbx_ref": done.result.sbx_ref, "original_action_id": done.action_id},
    )
    assert gov.gate.evaluate(cancel).decision == Decision.PENDING_APPROVAL
    blocked(
        gov,
        gov.act(
            ActionType.CANCEL_PAYMENT,
            {"sbx_ref": "pay_wrong", "original_action_id": done.action_id},
        ),
        ReasonCode.UNKNOWN_ENTITY,
    )
    today = gov.approve_execute(
        gov.gate.evaluate(
            gov.pay(bill_id="bil_paid", payee_id="pye_paid", bank_account_ref="SBX-PB-00000004")
        )
    )
    blocked(
        gov,
        gov.act(
            ActionType.CANCEL_PAYMENT,
            {"sbx_ref": today.result.sbx_ref, "original_action_id": today.action_id},
        ),
        ReasonCode.UNKNOWN_ENTITY,
    )


def test_tier3_path(gov: Any) -> None:
    """T-GOV-16 proves GOV-08: Tier 3 test type is blocked unless enabled; when enabled it
    executes within caps with approved_by=policy:tier3; over a cap it is downgraded."""

    def note(amount: int) -> Any:
        return gov.act(
            ActionType.TEST_T3_NOTE,
            {"amount_cents": amount, "note": "t3"},
            origin=Origin.SCHEDULE,
            by=SYSTEM,
            on_behalf_of=None,
        )

    blocked(gov, note(100), ReasonCode.BLOCKED_TYPE)
    gov.gate.update_setting("tier3_enabled_types", '["TEST_T3_NOTE"]', OWNER)
    d = gov.gate.evaluate(note(100))
    assert d.decision == Decision.EXECUTED and d.tier == Tier.T3_AUTONOMOUS
    assert gov.audit.rows()[-1].approved_by == "policy:tier3"
    over = gov.gate.evaluate(note(600_000))
    assert (
        over.decision == Decision.PENDING_APPROVAL
        and over.reason_code == ReasonCode.TIER3_DOWNGRADED
    )
    assert any(
        r.reason_code == ReasonCode.TIER3_DOWNGRADED and r.decision == Decision.EVENT
        for r in gov.audit.rows()
    )
    blocked(
        gov,
        gov.act(
            ActionType.TEST_T3_NOTE,
            {"amount_cents": 1, "note": "x"},
            origin=Origin.SCHEDULE,
            by=OWNER,
            on_behalf_of=None,
        ),
        ReasonCode.ROLE_NOT_ALLOWED,
    )


def test_overdue_invoice_entity_check_uses_lookup_only(gov: Any) -> None:
    """GOV-05: checks read entities only through the lookup, never from params."""
    gov.lookup.invoices = [make_invoice(due_date=date(2026, 10, 20))]
    blocked(gov, gov.reminder(), ReasonCode.NOT_OVERDUE)
