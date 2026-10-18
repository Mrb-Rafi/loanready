"""T-GOV-11..15, T-GOV-22: proposals, approval tokens, dynamic linking, expiry, replay,
maker-checker, and the PENDING -> APPROVED -> EXECUTED log trail."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from contracts.actions import AGENT, BOOKKEEPER, OWNER, Action, Principal
from contracts.enums import ActionType, Decision, PrincipalKind, ProposalStatus, ReasonCode, Role
from contracts.errors import ApprovalError, PermissionDenied


def _db_file(gov: Any) -> str:
    return gov.engine.url.database


def test_tier2_creates_proposal_with_summary(gov: Any) -> None:
    """T-GOV-11 proves PRD-03, UI-07: proposal with amount, payee, masked account, reason,
    sandbox target and cap usage; execute without token -> APPROVAL_REQUIRED."""
    d = gov.gate.evaluate(gov.pay())
    assert d.decision == Decision.PENDING_APPROVAL and d.proposal_id
    s = d.summary
    assert s.amount_cents == 80000 and s.counterparty_name == "Tiny Mills"
    assert s.payee_account_masked == "****0001" and s.currency == "USD"
    assert s.sandbox_target == "MockBank POST /bank/payments/schedule"
    assert s.cap_per_day_cents == 1_000_000 and s.cap_per_action_cents == 500_000
    assert "First payment to this payee in 30 days" in s.warnings
    assert [p.proposal_id for p in gov.gate.pending()] == [d.proposal_id]
    assert gov.gate.proposal(d.proposal_id).status == ProposalStatus.PENDING_APPROVAL
    e = gov.gate.execute(d.proposal_id, None, OWNER)
    assert e.decision == Decision.BLOCKED and e.reason_code == ReasonCode.APPROVAL_REQUIRED
    assert gov.executor.calls == []


def test_full_flow_logs_three_rows(gov: Any) -> None:
    """T-GOV-22 proves GOV-17, GOV-18: PENDING_APPROVAL, APPROVED, EXECUTED rows share the
    action_id; control ids populated; executor called once with a ticket."""
    action = gov.pay()
    d = gov.gate.evaluate(action)
    e = gov.approve_execute(d)
    assert e.decision == Decision.EXECUTED and e.result.ok and e.result.sbx_ref
    rows = [r for r in gov.audit.rows() if r.action_id == action.action_id]
    assert [r.decision for r in rows] == [
        Decision.PENDING_APPROVAL,
        Decision.APPROVED,
        Decision.EXECUTED,
    ]
    executed = rows[-1]
    assert executed.approved_by == "owner-rina" and executed.approved_at is not None
    assert executed.sbx_ref == e.result.sbx_ref
    for ctl in (
        "CTL-AC-01",
        "CTL-AC-06",
        "CTL-SD-01",
        "CTL-AU-01",
        "CTL-AU-02",
        "CTL-PAY-01",
        "CTL-PAY-02",
        "CTL-PAY-03",
        "CTL-PAY-04",
        "CTL-LIM-01",
        "CTL-LIM-02",
        "CTL-LIM-03",
        "CTL-LOG-01",
    ):
        assert ctl in executed.control_ids, ctl
    assert executed.params_masked["bank_account_ref"] == "****0001"
    assert len(gov.executor.calls) == 1
    act, ticket = gov.executor.calls[0]
    assert act.action_id == action.action_id and ticket.action_id == action.action_id
    assert gov.gate.proposal(d.proposal_id).status == ProposalStatus.EXECUTED
    assert gov.audit.verify_chain() is None


def _tamper_amount(gov: Any, proposal_id: str, field: str, value: Any) -> None:
    con = sqlite3.connect(_db_file(gov))
    (action_json,) = con.execute(
        "SELECT action_json FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    action = Action.model_validate_json(action_json)
    changed = action.model_copy(update={"params": {**action.params, field: value}})
    con.execute(
        "UPDATE proposals SET action_json = ? WHERE id = ?",
        (changed.model_dump_json(), proposal_id),
    )
    con.commit()
    con.close()


@settings(
    max_examples=25,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    field_value=st.one_of(
        st.tuples(
            st.just("amount_cents"),
            st.integers(min_value=1, max_value=79_999).filter(lambda x: x != 100),
        ),
        st.tuples(
            st.just("bank_account_ref"),
            st.text(alphabet="ABCDEFGHIJ0123456789-", min_size=4, max_size=20),
        ),
        st.tuples(st.just("payee_id"), st.sampled_from(["pye_paid", "pye_new", "pye_x"])),
        st.tuples(st.just("value_date"), st.sampled_from(["2026-10-15", "2026-11-01"])),
    )
)
def test_property_any_param_change_after_approval_fails(
    gov: Any, field_value: tuple[str, Any]
) -> None:
    """T-GOV-12 proves CTL-AU-01, REG-PAY-01 (property): any mutation of the stored params
    after approval -> APPROVAL_PARAMS_CHANGED, nothing executed."""
    field, value = field_value
    d = gov.gate.evaluate(gov.pay(amount_cents=100))
    token = gov.gate.approve(d.proposal_id, OWNER)
    _tamper_amount(gov, d.proposal_id, field, value)
    calls = len(gov.executor.calls)
    e = gov.gate.execute(d.proposal_id, token.token_id, OWNER)
    assert e.reason_code == ReasonCode.APPROVAL_PARAMS_CHANGED
    assert len(gov.executor.calls) == calls


def test_token_expiry(gov: Any) -> None:
    """T-GOV-13 proves CTL-AU-02: 901 s after approval -> APPROVAL_EXPIRED, proposal EXPIRED."""
    d = gov.gate.evaluate(gov.pay())
    token = gov.gate.approve(d.proposal_id, OWNER)
    assert (token.expires_at - token.approved_at).total_seconds() == 900
    gov.clock.advance(901)
    e = gov.gate.execute(d.proposal_id, token.token_id, OWNER)
    assert e.reason_code == ReasonCode.APPROVAL_EXPIRED
    assert gov.gate.proposal(d.proposal_id).status == ProposalStatus.EXPIRED


def test_token_replay_and_marked_used_before_executor(gov: Any) -> None:
    """T-GOV-14 proves CTL-AU-02: replay -> APPROVAL_REPLAY; the token is used even when the
    executor raises."""
    d = gov.gate.evaluate(gov.pay())
    token = gov.gate.approve(d.proposal_id, OWNER)
    assert gov.gate.execute(d.proposal_id, token.token_id, OWNER).decision == Decision.EXECUTED
    again = gov.gate.execute(d.proposal_id, token.token_id, OWNER)
    assert again.reason_code == ReasonCode.APPROVAL_REPLAY

    def boom(action: Any, ticket: Any) -> Any:
        raise RuntimeError("executor crashed")

    gov.executor.execute = boom  # type: ignore[method-assign]
    d2 = gov.gate.evaluate(
        gov.pay(bill_id="bil_paid", payee_id="pye_paid", bank_account_ref="SBX-PB-00000004")
    )
    t2 = gov.gate.approve(d2.proposal_id, OWNER)
    failed = gov.gate.execute(d2.proposal_id, t2.token_id, OWNER)
    assert failed.decision == Decision.FAILED and failed.reason_code == ReasonCode.SANDBOX_ERROR
    assert (
        gov.gate.execute(d2.proposal_id, t2.token_id, OWNER).reason_code
        == ReasonCode.APPROVAL_REPLAY
    )
    unknown = gov.gate.execute(d2.proposal_id, "0" * 32, OWNER)
    assert unknown.reason_code == ReasonCode.APPROVAL_INVALID


def test_maker_checker(gov: Any) -> None:
    """T-GOV-15 proves GOV-04, CTL-SD-01: a human maker cannot approve their own proposal;
    the owner may approve the agent's proposal made on the owner's behalf."""
    own = gov.gate.evaluate(
        gov.act(ActionType.SEND_REMINDER, gov.reminder().params, by=BOOKKEEPER, on_behalf_of=None)
    )
    with pytest.raises(ApprovalError) as exc:
        gov.gate.approve(own.proposal_id, BOOKKEEPER)
    assert exc.value.reason_code == ReasonCode.SELF_APPROVAL_FORBIDDEN
    assert gov.audit.rows()[-1].decision == Decision.BLOCKED
    assert gov.gate.approve(own.proposal_id, OWNER).approved_by == OWNER
    agent_made = gov.gate.evaluate(gov.pay())
    assert gov.gate.approve(agent_made.proposal_id, OWNER).approved_by.id == "owner-rina"


def test_role_rules_for_approval(gov: Any) -> None:
    """T-GOV-05 proves CTL-AC-04, CTL-SD-01: bookkeeper cannot approve payments; the agent
    cannot approve anything; bookkeeper may approve a reminder."""
    pay = gov.gate.evaluate(gov.pay())
    for who in (BOOKKEEPER, AGENT):
        with pytest.raises(PermissionDenied) as exc:
            gov.gate.approve(pay.proposal_id, who)
        assert exc.value.reason_code == ReasonCode.ROLE_NOT_ALLOWED
    rem = gov.gate.evaluate(gov.reminder())
    with pytest.raises(PermissionDenied):
        gov.gate.approve(rem.proposal_id, AGENT)
    assert gov.gate.approve(rem.proposal_id, BOOKKEEPER).approved_by.role == Role.BOOKKEEPER
    with pytest.raises(ApprovalError):
        gov.gate.approve("prp_missing", OWNER)
    with pytest.raises(ApprovalError):
        gov.gate.approve(rem.proposal_id, OWNER)  # no longer pending


def test_reject(gov: Any) -> None:
    """GOV-17: reject -> REJECTED row with REJECTED_BY_USER; status REJECTED; the agent
    cannot reject."""
    d = gov.gate.evaluate(gov.pay())
    with pytest.raises(PermissionDenied):
        gov.gate.reject(d.proposal_id, AGENT)
    r = gov.gate.reject(d.proposal_id, OWNER, "not now")
    assert r.decision == Decision.REJECTED and r.reason_code == ReasonCode.REJECTED_BY_USER
    assert gov.gate.proposal(d.proposal_id).status == ProposalStatus.REJECTED
    with pytest.raises(ApprovalError):
        gov.gate.reject(d.proposal_id, OWNER)
    with pytest.raises(ApprovalError):
        gov.gate.execute("prp_missing", None, OWNER)


def test_execute_requires_human_executor(gov: Any) -> None:
    """ROLE_RULES: only owner or bookkeeper execute."""
    d = gov.gate.evaluate(gov.pay())
    token = gov.gate.approve(d.proposal_id, OWNER)
    other = Principal(id="sys", kind=PrincipalKind.SYSTEM, role=Role.SYSTEM, display_name="s")
    assert (
        gov.gate.execute(d.proposal_id, token.token_id, other).reason_code
        == ReasonCode.ROLE_NOT_ALLOWED
    )
    assert gov.gate.execute(d.proposal_id, token.token_id, OWNER).decision == Decision.EXECUTED
