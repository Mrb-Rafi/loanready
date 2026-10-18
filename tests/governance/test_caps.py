"""T-GOV-10: caps per action, per day (pending approvals count), per payee per week,
messages per customer per week, and the 7-day reminder rate."""

from __future__ import annotations

from typing import Any

from contracts.actions import OWNER
from contracts.enums import Decision, ReasonCode


def test_per_action_cap(gov: Any) -> None:
    """T-GOV-10 proves CTL-LIM-01: an amount above cap_per_action_cents is blocked."""
    d = gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=500_001))
    assert d.decision == Decision.BLOCKED and d.reason_code == ReasonCode.CAP_PER_ACTION
    assert "CTL-LIM-01" in gov.audit.rows()[-1].control_ids
    assert (
        gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=500_000)).decision
        == Decision.PENDING_APPROVAL
    )


def test_per_day_cap_counts_pending(gov: Any) -> None:
    """T-GOV-10 proves CTL-LIM-02: two pending approvals cannot together exceed the day cap."""
    gov.gate.update_setting("cap_per_payee_week_cents", "5000000", OWNER)
    assert (
        gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=500_000)).decision
        == Decision.PENDING_APPROVAL
    )
    assert (
        gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=400_000)).decision
        == Decision.PENDING_APPROVAL
    )
    d = gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=100_001))
    assert d.reason_code == ReasonCode.CAP_PER_DAY
    tomorrow = gov.gate.evaluate(
        gov.pay(bill_id="bil_big", amount_cents=100_001, value_date="2026-10-15")
    )
    assert tomorrow.decision == Decision.PENDING_APPROVAL
    assert gov.gate.cap_usage().used_today_cents == 900_000


def test_rejected_proposals_do_not_count(gov: Any) -> None:
    """CTL-LIM-02: rejected proposals free the cap."""
    gov.gate.update_setting("cap_per_day_cents", "500000", OWNER)
    first = gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=500_000))
    assert (
        gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=1)).reason_code
        == ReasonCode.CAP_PER_DAY
    )
    gov.gate.reject(first.proposal_id, OWNER)
    assert (
        gov.gate.evaluate(gov.pay(bill_id="bil_big", amount_cents=1)).decision
        == Decision.PENDING_APPROVAL
    )


def test_per_payee_week_cap(gov: Any) -> None:
    """T-GOV-10 proves CTL-LIM-03: same payee within the rolling week."""
    gov.gate.update_setting("cap_per_payee_week_cents", "100000", OWNER)
    assert gov.gate.evaluate(gov.pay(amount_cents=60_000)).decision == Decision.PENDING_APPROVAL
    d = gov.gate.evaluate(gov.pay(amount_cents=50_000, value_date="2026-10-18"))
    assert d.reason_code == ReasonCode.CAP_PER_PAYEE_WEEK
    later = gov.gate.evaluate(gov.pay(amount_cents=50_000, value_date="2026-10-21"))
    assert later.decision == Decision.PENDING_APPROVAL


def test_reminder_rate_and_message_cap(gov: Any) -> None:
    """T-GOV-10 proves CTL-LIM-04, CTL-LIM-05, REG-COM-01: one reminder per invoice per 7
    days; messages per recipient per week capped."""
    first = gov.gate.evaluate(gov.reminder())
    assert gov.approve_execute(first).decision == Decision.EXECUTED
    again = gov.gate.evaluate(gov.reminder())
    assert again.reason_code == ReasonCode.RATE_LIMIT_REMINDER
    assert "CTL-LIM-05" in gov.audit.rows()[-1].control_ids
    gov.clock.advance(7 * 24 * 3600 + 1)
    assert gov.gate.evaluate(gov.reminder()).decision == Decision.PENDING_APPROVAL
    gov.gate.update_setting("cap_msgs_per_customer_week", "1", OWNER)
    capped = gov.gate.evaluate(gov.reminder())
    assert capped.reason_code == ReasonCode.CAP_MSGS_PER_CUSTOMER


def test_caps_rechecked_at_execution(gov: Any) -> None:
    """GOV §3.2 step 4: a cap lowered after approval stops execution."""
    d = gov.gate.evaluate(gov.pay(amount_cents=80_000))
    token = gov.gate.approve(d.proposal_id, OWNER)
    gov.gate.update_setting("cap_per_action_cents", "50000", OWNER)
    e = gov.gate.execute(d.proposal_id, token.token_id, OWNER)
    assert e.decision == Decision.BLOCKED and e.reason_code == ReasonCode.CAP_PER_ACTION
    assert gov.executor.calls == []
