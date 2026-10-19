"""Friendly messages for gate decisions and domain errors (UI-14).

Every message names the reason code. Wording is fixed text from code, never LLM output.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import streamlit as st

from contracts.actions import GateDecision
from contracts.enums import Decision, ReasonCode
from contracts.errors import LoanReadyError
from ui import state
from ui.components.money import md

REASON_TEXT: dict[ReasonCode, str] = {
    ReasonCode.KILL_SWITCH: "The kill switch is on, so nothing can be sent or paid. The owner "
    "can turn it off on the Log and security page.",
    ReasonCode.BLOCKED_TYPE: "This kind of action is never allowed in LoanReady, whoever asks.",
    ReasonCode.UNTRUSTED_ORIGIN: "The request came from text inside a document or message, "
    "which can never start an action.",
    ReasonCode.ROLE_NOT_ALLOWED: "The person you are acting as may not do this.",
    ReasonCode.INVALID_PARAMS: "Some details are missing or invalid.",
    ReasonCode.UNKNOWN_ENTITY: "We could not find that record.",
    ReasonCode.NEW_PAYEE: "This payee has never been paid before and is not pre-registered by "
    "the owner. Payments go only to known payees.",
    ReasonCode.ACCOUNT_CHANGED: "The bill asks for payment to a different bank account than the "
    "one we have on file for this supplier. Call the supplier on a known number to verify.",
    ReasonCode.PAYEE_BLOCKED: "This payee is blocked.",
    ReasonCode.WATCHLIST_HIT: "The payee matches a watch-list entry.",
    ReasonCode.AMOUNT_EXCEEDS_OUTSTANDING: "The amount is more than what is still owed.",
    ReasonCode.NOT_OVERDUE: "The invoice is not overdue yet.",
    ReasonCode.AMOUNT_MISMATCH: "The amount does not match the record.",
    ReasonCode.DRAFT_MISMATCH: "The message changed after it was drafted. Draft it again.",
    ReasonCode.PACK_HASH_MISMATCH: "The loan pack changed after it was generated. Generate it "
    "again.",
    ReasonCode.PERIOD_LOCKED: "That month is closed.",
    ReasonCode.CAP_PER_ACTION: "The amount is above the per-action limit.",
    ReasonCode.CAP_PER_DAY: "This would go over today's payment limit.",
    ReasonCode.CAP_PER_PAYEE_WEEK: "This would go over the weekly limit for this payee.",
    ReasonCode.CAP_MSGS_PER_CUSTOMER: "This customer already got the maximum number of messages "
    "this week.",
    ReasonCode.RATE_LIMIT_REMINDER: "A reminder was sent to this customer too recently.",
    ReasonCode.APPROVAL_REQUIRED: "This needs a human approval first.",
    ReasonCode.APPROVAL_INVALID: "The approval is not valid for this proposal.",
    ReasonCode.APPROVAL_EXPIRED: "The approval expired. Create a new proposal.",
    ReasonCode.APPROVAL_REPLAY: "That approval was already used.",
    ReasonCode.APPROVAL_PARAMS_CHANGED: "The details changed after approval, so the approval no "
    "longer applies.",
    ReasonCode.SELF_APPROVAL_FORBIDDEN: "You cannot approve your own proposal.",
    ReasonCode.TICKET_INVALID: "The execution ticket was not valid.",
    ReasonCode.SANDBOX_ERROR: "The sandbox service returned an error.",
    ReasonCode.INSUFFICIENT_FUNDS: "Not enough money in the account.",
    ReasonCode.REMOTE_NOT_ALLOWED: "This data may not be sent to a remote AI service.",
    ReasonCode.REJECTED_BY_USER: "Rejected.",
    ReasonCode.EXECUTED: "Done.",
    ReasonCode.BANK_LIMIT_EXCEEDED: "The bank's own limit was exceeded.",
    ReasonCode.RATE_LIMITED: "Too many requests; try again in a minute.",
    ReasonCode.PAYMENT_FAILED: "The payment failed at the bank.",
    ReasonCode.OK: "OK.",
}


def reason_text(code: ReasonCode) -> str:
    return REASON_TEXT.get(code, "See the reason code.")


def error_message(exc: LoanReadyError) -> str:
    """``"Blocked: <plain sentence> (REASON_CODE)"`` plus the exception's own message."""
    text = f"Blocked: {reason_text(exc.reason_code)} ({exc.reason_code.value})"
    if exc.message and exc.message not in text:
        text += f" Details: {exc.message}"
    return text


def decision_message(d: GateDecision) -> tuple[str, str]:
    """``(kind, text)`` for a gate decision; ``kind`` is a Streamlit alert name."""
    code = d.reason_code.value
    if d.decision == Decision.EXECUTED:
        ref = d.result.sbx_ref if d.result and d.result.sbx_ref else "none"
        return "success", f"Executed in the sandbox. Reference: {ref}. (log #{d.log_seq})"
    if d.decision == Decision.PENDING_APPROVAL:
        return "info", (
            f"Proposal created and waiting for approval in the approval queue ({code}, "
            f"log #{d.log_seq})."
        )
    if d.decision == Decision.DRAFTED:
        return "info", f"Draft created locally; nothing was sent (log #{d.log_seq})."
    if d.decision == Decision.ALLOWED:
        return "info", f"Allowed (log #{d.log_seq})."
    if d.decision == Decision.APPROVED:
        return "success", f"Approved (log #{d.log_seq})."
    if d.decision == Decision.REJECTED:
        return "info", f"Rejected; nothing was sent or paid ({code}, log #{d.log_seq})."
    if d.decision == Decision.BLOCKED:
        return "error", f"Blocked: {reason_text(d.reason_code)} ({code}, log #{d.log_seq})"
    return "error", f"Failed: {reason_text(d.reason_code)} ({code}, log #{d.log_seq})"


def show(kind: str, text: str) -> None:
    {"success": st.success, "info": st.info, "warning": st.warning, "error": st.error}.get(
        kind, st.info
    )(md(text))


def show_decision(d: GateDecision) -> None:
    show(*decision_message(d))


def flash_decision(d: GateDecision) -> None:
    state.flash(*decision_message(d))


def show_flashes() -> None:
    for kind, text in state.pop_flashes():
        show(kind, text)


def attempt[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T | None:
    """Run a facade write; show a friendly error and return ``None`` on a domain error."""
    try:
        return fn(*args, **kwargs)
    except LoanReadyError as exc:
        st.error(md(error_message(exc)))
        return None
    except (AssertionError, KeyError, LookupError, ValueError, TypeError) as exc:
        st.error(md(f"Could not do that: {type(exc).__name__} {exc}".strip()))
        return None


def attempt_ok(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> bool:
    """Like ``attempt`` for facade writes that return ``None``; ``True`` on success."""
    try:
        fn(*args, **kwargs)
    except LoanReadyError as exc:
        st.error(md(error_message(exc)))
        return False
    except (AssertionError, KeyError, LookupError, ValueError, TypeError) as exc:
        st.error(md(f"Could not do that: {type(exc).__name__} {exc}".strip()))
        return False
    return True
