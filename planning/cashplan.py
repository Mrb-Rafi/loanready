"""Cash plan: close the gap in cost order, COLLECT -> DELAY -> BORROW (PRD/06 §6;
PLN-08..PLN-10). Pure: each lever is a what-if on a copy of the inputs and the BASE
forecast is recomputed after it; the real forecast never changes until money moves.

1. COLLECT (no cost): every invoice overdue at AS_OF by at most 60 days is assumed paid at
   AS_OF + 5. One DRAFT_REMINDER draft per invoice.
2. DELAY (no cost within terms): bills with no late penalty, not essential, not rent /
   wages / loan / tax, grace_days > 0 and due in weeks 1-4 move to due + grace_days,
   largest improvement first, until the gap is closed or no move helps. One
   DRAFT_DELAY_REQUEST draft per moved bill.
3. BORROW: a FinancingRequest for the remaining gap, with the term recomputed after 1-2.
Gaps are BASE gaps: roundup_100(max(0, -min BASE closing)). The plan proposes Tier 1
drafts only, never Tier 2 actions (PLN-09).
"""

from __future__ import annotations

from datetime import timedelta

from contracts.dto_financing import EligibleInvoice, FinancingRequest
from contracts.dto_planning import (
    CashPlanDTO,
    CashPlanStep,
    ForecastDTO,
    ForecastInputs,
    ProposedDraft,
)
from contracts.enums import ActionType, Lever, Scenario
from contracts.money import fmt
from planning.forecast import bill_week, run_scenario
from planning.gap import base_gap, needed_term_days
from planning.scenarios import OVERDUE_EXCLUDED_DAYS, collection_date

NEVER_DELAY = frozenset({"6000", "6100", "2500", "7000", "TAX"})
COLLECT_AFTER_DAYS = 5


def _gap(inputs: ForecastInputs) -> int:
    return base_gap(run_scenario(inputs, Scenario.BASE))


def _step(lever: Lever, before: int, after: int, refs: list[str]) -> CashPlanStep:
    return CashPlanStep(
        lever=lever,
        gap_before_cents=before,
        gap_after_cents=after,
        closed_cents=before - after,
        item_refs=refs,
        explanation_facts={
            "closed": fmt(before - after),
            "remaining": fmt(after),
            "items": ", ".join(refs),
        },
    )


def delayable(inputs: ForecastInputs) -> list[str]:
    """PLN-08: essential obligations (rent, wages, tax, loan) are never delayed."""
    return sorted(
        b.bill_id
        for b in inputs.open_bills
        if not b.late_penalty
        and not b.essential
        and b.category.upper() not in NEVER_DELAY
        and b.grace_days > 0
        and (bill_week(b.due_date, inputs.as_of) or 99) <= 4
    )


def plan(
    inputs: ForecastInputs, forecast: ForecastDTO, avg_daily_card_sales_cents: int
) -> CashPlanDTO:
    g0 = forecast.base_gap_cents
    if g0 == 0:
        return CashPlanDTO(
            as_of=inputs.as_of,
            initial_gap_cents=0,
            steps=[],
            drafts=[],
            financing_request=None,
            remaining_gap_cents=0,
        )
    as_of = inputs.as_of
    steps: list[CashPlanStep] = []
    drafts: list[ProposedDraft] = []
    current, gap = inputs, g0

    # 1. COLLECT
    overdue = sorted(
        i.invoice_id
        for i in inputs.open_invoices
        if 0 < (as_of - i.due_date).days <= OVERDUE_EXCLUDED_DAYS
    )
    if overdue:
        paid_on = as_of + timedelta(days=COLLECT_AFTER_DAYS)
        current = current.model_copy(
            update={
                "open_invoices": [
                    i.model_copy(update={"due_date": paid_on, "lateness_history_days": [0]})
                    if i.invoice_id in overdue
                    else i
                    for i in current.open_invoices
                ]
            }
        )
        after = _gap(current)
        steps.append(_step(Lever.COLLECT, gap, after, overdue))
        drafts += [
            ProposedDraft(
                action_type=ActionType.DRAFT_REMINDER,
                params={"invoice_id": inv_id},
                reason="Invoice overdue at AS_OF; a reminder may bring the payment forward",
            )
            for inv_id in overdue
        ]
        gap = after

    # 2. DELAY
    if gap > 0:
        candidates = delayable(current)
        moved: list[str] = []
        before_delay = gap
        while gap > 0 and candidates:
            options = []
            for bill_id in candidates:
                trial = _move(current, bill_id)
                options.append((gap - _gap(trial), bill_id, trial))
            best_gain, best_id, best_inputs = min(options, key=lambda o: (-o[0], o[1]))
            if best_gain <= 0:
                break
            current, gap = best_inputs, gap - best_gain
            candidates.remove(best_id)
            moved.append(best_id)
        if moved:
            steps.append(_step(Lever.DELAY, before_delay, gap, moved))
            originals = {b.bill_id: b for b in inputs.open_bills}
            drafts += [
                ProposedDraft(
                    action_type=ActionType.DRAFT_DELAY_REQUEST,
                    params={
                        "bill_id": bill_id,
                        "proposed_date": (
                            originals[bill_id].due_date
                            + timedelta(days=originals[bill_id].grace_days)
                        ).isoformat(),
                    },
                    reason="No late penalty, not essential, within the supplier's grace period",
                )
                for bill_id in moved
            ]

    # 3. BORROW
    request = None
    if gap > 0:
        base = run_scenario(current, Scenario.BASE)
        eligible = []
        for i in sorted(inputs.open_invoices, key=lambda x: x.invoice_id):
            if i.due_date < as_of:
                continue
            when = collection_date(i, Scenario.BASE, as_of)
            if when is not None:
                eligible.append(
                    EligibleInvoice(
                        invoice_id=i.invoice_id,
                        outstanding_cents=i.outstanding_cents,
                        expected_days_to_pay=(when - as_of).days,
                    )
                )
        request = FinancingRequest(
            amount_cents=gap,
            term_days=needed_term_days(base, as_of.toordinal()),
            as_of=as_of,
            avg_daily_card_sales_cents=avg_daily_card_sales_cents,
            eligible_invoices=eligible,
            # ForecastInputs carries no supplier payment terms; any open bill is taken as
            # a sign that supplier credit is available (README, proposed CCR).
            supplier_terms_available=bool(inputs.open_bills),
        )
        steps.append(_step(Lever.BORROW, gap, gap, []))
    return CashPlanDTO(
        as_of=as_of,
        initial_gap_cents=g0,
        steps=steps,
        drafts=drafts,
        financing_request=request,
        remaining_gap_cents=gap,
    )


def _move(inputs: ForecastInputs, bill_id: str) -> ForecastInputs:
    return inputs.model_copy(
        update={
            "open_bills": [
                b.model_copy(update={"due_date": b.due_date + timedelta(days=b.grace_days)})
                if b.bill_id == bill_id
                else b
                for b in inputs.open_bills
            ]
        }
    )
