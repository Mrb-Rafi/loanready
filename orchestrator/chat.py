"""Chat handlers (PRD/11 ORC-05, ORC-06; P1).

The agent only **picks an intent** from the fixed menu (and may extract a payee name or an
amount the owner typed). Each handler calls facade methods, and every number in a reply is
formatted here by code with ``contracts.money.fmt``. Text worded by the agent (forecast and
financing explanations) is attached with ``ai_label``.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from contracts.actions import GateDecision
from contracts.dto_app import ChatReply
from contracts.dto_books import PayeeDTO
from contracts.enums import Decision, Intent, Scenario
from contracts.errors import LoanReadyError
from contracts.money import fmt, parse

if TYPE_CHECKING:
    from orchestrator.facade import App

AI_LABEL = "AI-generated explanation of figures calculated by LoanReady"
FUZZY_MIN = 0.85
DEFAULT_PACK_TERM_DAYS = 90
DEFAULT_PACK_PURPOSE = "Working capital"
HELP_TEXT = (
    "I can show your cash position, who owes you money, draft payment reminders, plan "
    "which bills to delay, compare financing, build a loan pack, schedule a supplier "
    "payment for your approval, show your loan readiness and the audit log."
)


def _reply(
    text: str,
    intent: Intent,
    *,
    ai: bool = False,
    attachments: list[str] | None = None,
    proposals: list[str] | None = None,
) -> ChatReply:
    return ChatReply(
        text=text,
        ai_label=AI_LABEL if ai else None,
        intent=intent,
        attachments=attachments or [],
        proposals=proposals or [],
    )


def _money_param(params: dict[str, Any], key: str) -> int | None:
    """An amount the owner typed, re-parsed by code (never taken as a model number)."""
    raw = params.get(key)
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw if key.endswith("_cents") else raw * 100
    try:
        return parse(str(raw))
    except ValueError:
        return None


def _int_param(params: dict[str, Any], key: str) -> int | None:
    raw = params.get(key)
    try:
        return int(str(raw)) if raw is not None else None
    except ValueError:
        return None


def match_payee(name: str, payees: list[PayeeDTO]) -> PayeeDTO | None:
    """Exact (case-insensitive) match, else the best fuzzy match with ratio >= 0.85."""
    wanted = name.strip().lower()
    for p in payees:
        if p.name.lower() == wanted:
            return p
    best, score = None, 0.0
    for p in payees:
        r = difflib.SequenceMatcher(None, wanted, p.name.lower()).ratio()
        if r > score:
            best, score = p, r
    return best if score >= FUZZY_MIN else None


def _proposal_text(d: GateDecision, what: str) -> tuple[str, list[str]]:
    if d.decision == Decision.PENDING_APPROVAL and d.proposal_id:
        return f"{what} is waiting for your approval.", [d.proposal_id]
    return f"{what} was {d.decision.value.lower()}: {d.reason_code.value}.", []


def cash_position(app: App) -> ChatReply:
    f = app.forecast()
    base = f.scenarios[Scenario.BASE]
    cash = fmt(app.books.cash_balance(app.as_of))
    if base.first_negative_week is None:
        facts = f"Cash today is {cash}. The base case stays above zero for 13 weeks."
    else:
        facts = (
            f"Cash today is {cash}. The base case goes below zero in week "
            f"{base.first_negative_week}; the recommended buffer to cover is "
            f"{fmt(f.recommended_gap_cents)}."
        )
    explanation = app.agent.explain_forecast(f)
    return _reply(f"{facts}\n\n{explanation}", Intent.CASH_POSITION, ai=True)


def who_owes_me(app: App) -> ChatReply:
    ageing = app.receivables()
    rows = [r for r in ageing.rows if r.outstanding_cents > 0]
    if not rows:
        return _reply("Nobody owes you money right now.", Intent.WHO_OWES_ME)
    lines = [f"{r.customer_name}: {fmt(r.outstanding_cents)}" for r in rows]
    total = sum(r.outstanding_cents for r in rows)
    return _reply(
        "Customers who owe you:\n" + "\n".join(lines) + f"\nTotal {fmt(total)}.",
        Intent.WHO_OWES_ME,
    )


def draft_reminders(app: App) -> ChatReply:
    drafts = app.draft_reminders()
    if not drafts:
        return _reply("No invoice is 1 to 60 days overdue.", Intent.DRAFT_REMINDERS)
    proposals, blocked = [], 0
    for d in drafts:
        dec = app.propose_send(d.draft_id)
        if dec.decision == Decision.PENDING_APPROVAL and dec.proposal_id:
            proposals.append(dec.proposal_id)
        else:
            blocked += 1
    text = f"I drafted {len(drafts)} reminder(s). Approve sending?"
    if blocked:
        text += f" {blocked} could not be proposed (see the log)."
    return _reply(text, Intent.DRAFT_REMINDERS, proposals=proposals)


def delay_bills(app: App) -> ChatReply:
    plan = app.cash_plan()
    drafts = app.draft_delay_requests()
    delay = [s for s in plan.steps if s.lever.value == "DELAY"]
    if not drafts:
        return _reply("No bill needs to be delayed in the current plan.", Intent.DELAY_BILLS)
    closed = sum(s.closed_cents for s in delay)
    proposals = []
    for d in drafts:
        dec = app.propose_send(d.draft_id)
        if dec.decision == Decision.PENDING_APPROVAL and dec.proposal_id:
            proposals.append(dec.proposal_id)
    return _reply(
        f"Delaying {len(drafts)} penalty-free bill(s) closes {fmt(closed)} of the gap. "
        "Approve sending the requests?",
        Intent.DELAY_BILLS,
        proposals=proposals,
    )


def compare_financing(app: App, params: dict[str, Any]) -> ChatReply:
    amount = _money_param(params, "amount_cents") or _money_param(params, "amount")
    term = _int_param(params, "term_days")
    comp = app.financing(amount, term)
    by_id = {o.product_id: o for o in comp.options}
    cheap = by_id.get(comp.cheapest_id or "")
    dear = by_id.get(comp.most_expensive_id or "")
    facts = f"For {fmt(comp.request.amount_cents)} over {comp.request.term_days} days: "
    if cheap and dear:
        facts += (
            f"cheapest is {cheap.name} (total cost {fmt(cheap.total_cost_cents)}), most "
            f"expensive is {dear.name} (total cost {fmt(dear.total_cost_cents)})."
        )
    else:
        facts += "no eligible option."
    return _reply(
        f"{facts}\n\n{app.agent.explain_financing(comp)}", Intent.COMPARE_FINANCING, ai=True
    )


def build_loan_pack(app: App, params: dict[str, Any]) -> ChatReply:
    amount = _money_param(params, "amount_cents") or _money_param(params, "amount")
    if amount is None:
        amount = app.forecast().recommended_gap_cents or 100000
    term = _int_param(params, "term_days") or DEFAULT_PACK_TERM_DAYS
    pack = app.build_loan_pack(amount, term, DEFAULT_PACK_PURPOSE)
    dec = app.propose_submit_pack(pack.pack_id)
    what = f"Loan pack for {fmt(amount)} over {term} days built; submitting it"
    text, proposals = _proposal_text(dec, what)
    return _reply(text, Intent.BUILD_LOAN_PACK, attachments=[pack.path], proposals=proposals)


def schedule_payment(app: App, params: dict[str, Any]) -> ChatReply:
    name = str(params.get("payee") or params.get("payee_name") or "").strip()
    if not name:
        return _reply("Which supplier should I pay?", Intent.SCHEDULE_PAYMENT)
    payee = match_payee(name, app.books.payees())
    if payee is None:
        return _reply(
            "I could not find that supplier among your known payees. Which one did you mean?",
            Intent.SCHEDULE_PAYMENT,
        )
    bills = sorted(
        (b for b in app.books.open_bills() if b.payee_id == payee.id),
        key=lambda b: (b.due_date, b.issue_date, b.id),
    )
    if not bills:
        return _reply(f"There is no open bill from {payee.name}.", Intent.SCHEDULE_PAYMENT)
    bill = bills[0]
    dec = app.propose_supplier_payment(bill.id)
    text, proposals = _proposal_text(
        dec, f"Payment of {fmt(bill.outstanding_cents)} for {bill.number} to {payee.name}"
    )
    return _reply(text, Intent.SCHEDULE_PAYMENT, proposals=proposals)


def readiness(app: App) -> ChatReply:
    r = app.readiness()
    return _reply(f"Your loan-readiness self-assessment is {r.score} out of 100.", Intent.READINESS)


def show_log(app: App) -> ChatReply:
    counts = app.audit_counts()
    chain = "intact" if app.verify_chain() is None else "BROKEN"
    parts = ", ".join(f"{k.lower()} {v}" for k, v in sorted(counts.items()) if v)
    return _reply(f"Audit log: {parts or 'empty'}. Hash chain {chain}.", Intent.SHOW_LOG)


def handle(app: App, message: str) -> ChatReply:
    """Route, then run the handler for the intent; errors become a plain reply."""
    r = app.agent.route(message)
    params = dict(r.params)
    try:
        if r.intent == Intent.CASH_POSITION:
            return cash_position(app)
        if r.intent == Intent.WHO_OWES_ME:
            return who_owes_me(app)
        if r.intent == Intent.DRAFT_REMINDERS:
            return draft_reminders(app)
        if r.intent == Intent.DELAY_BILLS:
            return delay_bills(app)
        if r.intent == Intent.COMPARE_FINANCING:
            return compare_financing(app, params)
        if r.intent == Intent.BUILD_LOAN_PACK:
            return build_loan_pack(app, params)
        if r.intent == Intent.SCHEDULE_PAYMENT:
            return schedule_payment(app, params)
        if r.intent == Intent.READINESS:
            return readiness(app)
        if r.intent == Intent.SHOW_LOG:
            return show_log(app)
    except LoanReadyError as exc:
        return _reply(f"I could not do that: {exc.message}", r.intent)
    return _reply(HELP_TEXT, Intent.HELP if r.intent == Intent.HELP else Intent.UNKNOWN)
