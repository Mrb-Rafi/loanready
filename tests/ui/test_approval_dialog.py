"""Approval dialog (PRD/12 §3, UI-07, UI-08; PRD/15 §4.10 T-UI-02, T-UI-03)."""

from __future__ import annotations

from datetime import UTC, datetime

from streamlit.testing.v1 import AppTest

from contracts.actions import ApprovalSummary
from contracts.enums import ActionType, Role, Tier
from tests.ui.conftest import TIMEOUT
from ui.components.approval_dialog import build_view, expiry_text, may_approve


def _summary(action_type: ActionType = ActionType.SCHEDULE_SUPPLIER_PAYMENT) -> ApprovalSummary:
    return ApprovalSummary(
        proposal_id="prp_test",
        action_type=action_type,
        tier=Tier.T2_APPROVE,
        amount_cents=500000,
        currency="USD",
        counterparty_name="Golden Mills",
        payee_account_masked="****2277",
        reason="Pay BILL-2 to Golden Mills",
        sandbox_target="MockBank POST /bank/payments/schedule",
        cap_used_today_cents=12345,
        cap_per_day_cents=1000000,
        cap_per_action_cents=500000,
        warnings=["Value date is a weekend"],
        expires_at=datetime(2026, 10, 14, 3, 15, tzinfo=UTC),
    )


def test_view_has_required_fields() -> None:
    """T-UI-02 proves UI-07, REG-PAY-02: amount, payee, masked account, reason, target, caps."""
    v = build_view(_summary(), Role.OWNER, kill_switch=False)
    assert v.amount == "$5,000.00"
    assert v.payee == "Golden Mills"
    assert v.payee_account == "****2277"
    assert v.reason == "Pay BILL-2 to Golden Mills"
    assert v.sandbox_target == "MockBank POST /bank/payments/schedule"
    assert v.cap_today == "$123.45 used of $10,000.00 per day"
    assert v.cap_per_action == "$5,000.00"
    assert v.tier_text == "Tier 2: needs your approval"
    assert v.warnings == ["Value date is a weekend"]
    assert "valid for 15 minutes and only for exactly these details" in v.expiry
    assert v.can_approve and v.disabled_reason is None


def test_bookkeeper_cannot_approve_payment() -> None:
    """T-UI-03 proves PRD/03 §2 role rules in the UI: bookkeeper approve disabled for payments."""
    v = build_view(_summary(), Role.BOOKKEEPER, kill_switch=False)
    assert not v.can_approve
    assert v.disabled_reason and "bookkeeper cannot approve payments" in v.disabled_reason
    assert may_approve(Role.BOOKKEEPER, ActionType.SEND_REMINDER) == (True, None)
    assert may_approve(Role.BOOKKEEPER, ActionType.CANCEL_PAYMENT)[0] is False
    assert may_approve(Role.AGENT, ActionType.SEND_REMINDER)[0] is False


def test_kill_switch_disables_approve() -> None:
    """T-UI-03 proves PRD/12 §3, CTL-AC-07: approve disabled with explanation when kill is on."""
    v = build_view(_summary(ActionType.SEND_REMINDER), Role.OWNER, kill_switch=True)
    assert not v.can_approve and "KILL_SWITCH" in (v.disabled_reason or "")


def test_contact_masked_and_no_amount() -> None:
    """T-UI-02 proves UI-07: contacts masked; messages show that nothing is paid."""
    s = _summary(ActionType.SEND_REMINDER).model_copy(
        update={"amount_cents": None, "counterparty_name": "ap@tiny-catering.test"}
    )
    v = build_view(s, Role.OWNER, kill_switch=False)
    assert v.payee == "a***@tiny-catering.test"
    assert v.amount == "No amount (nothing is paid)"
    assert expiry_text(None, 600).startswith("This approval is valid for 10 minutes")


def _dialog_script() -> None:
    import streamlit as st

    from contracts.enums import Role
    from ui import state
    from ui.components.approval_dialog import render_approval

    app = state.get_app()
    if "pid" not in st.session_state:
        d = app.propose_supplier_payment(st.session_state.get("bill", "bil_tiny_1"))
        st.session_state["pid"] = d.proposal_id
    state.set_session_role(st.session_state.get("want_role", Role.OWNER))
    state.ensure_principal(app)
    render_approval(app, st.session_state["pid"])
    from ui.components import feedback

    feedback.show_flashes()


def _run_dialog(role: Role, bill: str = "bil_tiny_2") -> AppTest:
    at = AppTest.from_function(_dialog_script, default_timeout=TIMEOUT)
    at.session_state["want_role"] = role
    at.session_state["bill"] = bill
    at.run()
    assert not at.exception, at.exception
    return at


def _all_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [i.value for i in at.info] + [e.value for e in at.error]
    return "\n".join(parts).replace(r"\$", "$")  # markdown escapes $ (LaTeX)


def test_dialog_renders_summary_from_gate() -> None:
    """T-UI-02 proves UI-07, UI-08: the rendered dialog shows the gate summary, no edit fields."""
    at = _run_dialog(Role.OWNER)
    text = _all_text(at)
    assert "$5,000.00" in text
    assert "****0001" in text
    assert "Pay BILL-2 to Tiny Mills" in text
    assert "MockBank POST /bank/payments/schedule" in text
    assert "used of $10,000.00 per day" in text
    assert "valid for 15 minutes" in text
    labels = [t.label for t in at.text_input]
    assert labels == ["Rejection note (optional)"]  # UI-08: no amount or payee inputs
    approve = next(b for b in at.button if b.label == "Approve and execute")
    assert not approve.disabled


def test_dialog_bookkeeper_approve_disabled() -> None:
    """T-UI-03 proves PRD/12 §6: bookkeeper sees Approve disabled for a payment."""
    at = _run_dialog(Role.BOOKKEEPER)
    approve = next(b for b in at.button if b.label == "Approve and execute")
    assert approve.disabled
    assert "bookkeeper cannot approve payments" in _all_text(at)


def test_dialog_owner_approves_and_executes() -> None:
    """T-UI-01 proves REG-AI-06 human oversight: owner approval executes in the sandbox."""
    at = _run_dialog(Role.OWNER, bill="bil_tiny_1")  # $800.00, within the fake balance
    next(b for b in at.button if b.label == "Approve and execute").click().run()
    assert not at.exception, at.exception
    assert any("Executed in the sandbox" in s.value for s in at.success)


def test_dialog_sandbox_error_is_friendly() -> None:
    """T-UI-01 proves UI-14: a sandbox refusal is shown with its reason code, no crash."""
    at = _run_dialog(Role.OWNER)  # $5,000.00 is more than the fake bank balance
    next(b for b in at.button if b.label == "Approve and execute").click().run()
    assert not at.exception, at.exception
    assert any("(INSUFFICIENT_FUNDS)" in e.value for e in at.error)


def test_dialog_reject() -> None:
    """T-UI-01 proves PRD/12 §3: Reject records a rejection; nothing is paid."""
    at = _run_dialog(Role.OWNER)
    next(b for b in at.button if b.label == "Reject").click().run()
    assert not at.exception
    assert any("Rejected" in i.value for i in at.info)
