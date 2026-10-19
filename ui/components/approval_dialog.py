"""The human approval screen for Tier 2 proposals (PRD/12 §3; UI-07, UI-08; REG-PAY-02,
REG-AI-06; CTL-SD-01).

Everything shown comes from the gate's ``ApprovalSummary`` (code, never the LLM). There are
no editable amount or payee fields: to change anything the user rejects and creates a new
proposal, which gets a new params hash (UI-08).

The approve button is disabled, with the reason, when the session principal may not approve
this action type (PRD/03 §2 role table: bookkeepers cannot approve payment types) or when
the kill switch is on. This is a display courtesy only: the gate re-checks role, kill switch,
expiry and params hash on approve and execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from contracts.actions import ApprovalSummary, Proposal
from contracts.enums import ActionType, ProposalStatus, Role
from contracts.services import AppFacade
from ui import state
from ui.components import feedback
from ui.components.badges import tier_badge, tier_text
from ui.components.money import md, money
from ui.components.tables import display_party

# PRD/03 §2 ROLE_RULES, "Approve Tier 2 payment types".
PAYMENT_TYPES = frozenset({ActionType.SCHEDULE_SUPPLIER_PAYMENT, ActionType.CANCEL_PAYMENT})
APPROVER_ROLES = frozenset({Role.OWNER, Role.BOOKKEEPER})
DEFAULT_TTL_SECONDS = 900


@dataclass(frozen=True)
class ApprovalView:
    """What the dialog shows, as plain strings (testable without Streamlit)."""

    proposal_id: str
    action_type: str
    tier_text: str
    amount: str
    currency: str
    payee: str
    payee_account: str
    reason: str
    sandbox_target: str
    cap_today: str
    cap_per_action: str
    warnings: list[str]
    expiry: str
    can_approve: bool
    disabled_reason: str | None


def may_approve(role: Role, action_type: ActionType) -> tuple[bool, str | None]:
    """PRD/03 §2 role rule as shown to the user; ``(allowed, why_not)``."""
    if role not in APPROVER_ROLES:
        return False, "Only a human owner or bookkeeper can approve."
    if role == Role.BOOKKEEPER and action_type in PAYMENT_TYPES:
        return False, (
            "A bookkeeper cannot approve payments. Switch to the owner in the sidebar "
            "(ROLE_NOT_ALLOWED)."
        )
    return True, None


def expiry_text(expires_at: datetime | None, ttl_seconds: int) -> str:
    minutes = max(1, ttl_seconds // 60)
    text = f"This approval is valid for {minutes} minutes and only for exactly these details."
    if expires_at is not None:
        text += f" The proposal expires at {expires_at.strftime('%H:%M:%S UTC on %d %b %Y')}."
    return text


def build_view(
    summary: ApprovalSummary,
    role: Role,
    kill_switch: bool,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    status: ProposalStatus = ProposalStatus.PENDING_APPROVAL,
) -> ApprovalView:
    allowed, why = may_approve(role, summary.action_type)
    if allowed and kill_switch:
        allowed, why = False, "The kill switch is on, so nothing can be executed (KILL_SWITCH)."
    if allowed and status != ProposalStatus.PENDING_APPROVAL:
        allowed, why = False, f"This proposal is {status.value}, not waiting for approval."
    return ApprovalView(
        proposal_id=summary.proposal_id,
        action_type=summary.action_type.value,
        tier_text=tier_text(summary.tier),
        amount=money(summary.amount_cents, summary.currency)
        if summary.amount_cents is not None
        else "No amount (nothing is paid)",
        currency=summary.currency,
        payee=display_party(summary.counterparty_name) or "n/a",
        payee_account=summary.payee_account_masked or "n/a",
        reason=summary.reason,
        sandbox_target=summary.sandbox_target or "n/a",
        cap_today=f"{money(summary.cap_used_today_cents, summary.currency)} used of "
        f"{money(summary.cap_per_day_cents, summary.currency)} per day",
        cap_per_action=money(summary.cap_per_action_cents, summary.currency),
        warnings=list(summary.warnings),
        expiry=expiry_text(summary.expires_at, ttl_seconds),
        can_approve=allowed,
        disabled_reason=why,
    )


def _ttl(app: AppFacade) -> int:
    raw = state.cached(app, "settings").get("approval_ttl_seconds", "")
    return int(raw) if str(raw).isdigit() else DEFAULT_TTL_SECONDS


def _find(app: AppFacade, proposal_id: str) -> Proposal | None:
    return next((p for p in app.pending() if p.proposal_id == proposal_id), None)


def render_approval(app: AppFacade, proposal_id: str) -> None:
    """Dialog body. Also used directly by tests (st.dialog is only the frame)."""
    prop = _find(app, proposal_id)
    if prop is None:
        st.warning("This proposal is no longer waiting for approval.")
        return
    kill = bool(state.cached(app, "dashboard").kill_switch)
    view = build_view(prop.summary, state.session_role(), kill, _ttl(app), prop.status)

    st.markdown(f"**{view.action_type}** &nbsp; {tier_badge(prop.summary.tier)}")
    st.caption("Amount")
    st.markdown(f"## {md(view.amount)}")
    st.caption(f"Currency: {view.currency}")
    c1, c2 = st.columns(2)
    c1.markdown(f"**Payee / recipient**  \n{view.payee}")
    c2.markdown(f"**Account**  \n{view.payee_account}")
    st.markdown(f"**Reason**  \n{md(view.reason)}")
    st.markdown(f"**Sandbox target**  \n`{view.sandbox_target}`")
    c3, c4 = st.columns(2)
    c3.markdown(f"**Cap usage today**  \n{md(view.cap_today)}")
    c4.markdown(f"**Per-action cap**  \n{md(view.cap_per_action)}")
    for w in view.warnings:
        st.warning(md(w))
    st.info(md(view.expiry))
    st.caption(f"Proposal {view.proposal_id}. To change anything, reject and create a new one.")
    if view.disabled_reason:
        st.error(md(view.disabled_reason))

    note = st.text_input("Rejection note (optional)", key=f"lr_reject_note_{proposal_id}")
    b1, b2 = st.columns(2)
    approve = b1.button(
        "Approve and execute",
        type="primary",
        disabled=not view.can_approve,
        key=f"lr_approve_{proposal_id}",
        help=view.disabled_reason,
    )
    reject = b2.button("Reject", key=f"lr_reject_{proposal_id}")
    if approve and view.can_approve:
        _approve_and_execute(app, prop)
    elif reject:
        decision = feedback.attempt(app.reject, proposal_id, note)
        if decision is not None:
            feedback.flash_decision(decision)
            st.rerun()


def _approve_and_execute(app: AppFacade, prop: Proposal) -> None:
    pid = prop.proposal_id
    token = feedback.attempt(app.approve, pid)
    if token is None:
        return
    state.remember_token(pid, token)
    decision = feedback.attempt(app.execute, pid)
    state.forget_token(pid)
    if decision is None:
        return
    feedback.flash_decision(decision)
    if prop.summary.action_type == ActionType.SUBMIT_LOAN_PACK:
        state.set_last(state.LAST_SUBMISSION, decision)
    st.rerun()


@st.dialog("Approval needed", width="large")
def approval_dialog(app: AppFacade, proposal_id: str) -> None:
    render_approval(app, proposal_id)


def review_button(app: AppFacade, proposal: Proposal, label: str = "Review and approve") -> None:
    """A button that opens the approval dialog for one proposal."""
    if st.button(label, key=f"lr_review_{proposal.proposal_id}"):
        approval_dialog(app, proposal.proposal_id)
