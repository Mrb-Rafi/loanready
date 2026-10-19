"""T-PLN-08 proves PRD-14, PLN-08..PLN-10: COLLECT -> DELAY -> BORROW order; essential
never delayed; grace respected; remaining gap -> FinancingRequest; zero gap -> no steps."""

from __future__ import annotations

from datetime import date

from contracts.dto_books import RecurringObligation
from contracts.dto_planning import BillForecastItem, ForecastInputs
from contracts.enums import ActionType, Lever
from contracts.testing.builders import make_forecast_inputs_tiny
from planning.cashplan import delayable
from planning.service import Planner


def bill(bid: str, due: date, cents: int, **kw: object) -> BillForecastItem:
    base: dict[str, object] = dict(
        bill_id=bid,
        supplier_id="sup",
        due_date=due,
        outstanding_cents=cents,
        category="5000",
        late_penalty=False,
        essential=False,
        grace_days=14,
    )
    base.update(kw)
    return BillForecastItem(**base)  # type: ignore[arg-type]


def run(inputs: ForecastInputs) -> object:
    p = Planner()
    return p.plan(inputs, p.forecast(inputs), 25000)


def test_zero_gap_no_steps() -> None:
    """T-PLN-08: no gap -> no steps, no drafts, no financing request."""
    plan = run(make_forecast_inputs_tiny())
    assert plan.steps == [] and plan.drafts == [] and plan.financing_request is None  # type: ignore[attr-defined]


def test_crunch_collect_closes_gap() -> None:
    """T-PLN-08 / PRD/15 crunch: COLLECT moves INV-1 to 10-19 -> gap 0; one reminder."""
    plan = run(make_forecast_inputs_tiny(crunch=True))
    assert [s.lever for s in plan.steps] == [Lever.COLLECT]  # type: ignore[attr-defined]
    step = plan.steps[0]  # type: ignore[attr-defined]
    assert (step.gap_before_cents, step.gap_after_cents, step.item_refs) == (
        50000,
        0,
        ["inv_tiny_1"],
    )
    assert step.explanation_facts == {
        "closed": "$500.00",
        "remaining": "$0.00",
        "items": "inv_tiny_1",
    }
    assert [(d.action_type, d.params) for d in plan.drafts] == [  # type: ignore[attr-defined]
        (ActionType.DRAFT_REMINDER, {"invoice_id": "inv_tiny_1"})
    ]
    assert plan.financing_request is None and plan.remaining_gap_cents == 0  # type: ignore[attr-defined]


def _three_lever_inputs() -> ForecastInputs:
    base = make_forecast_inputs_tiny(crunch=True)
    bills = [
        bill("big", date(2026, 10, 28), 700000, late_penalty=True, grace_days=0),
        bill("delay_me", date(2026, 10, 27), 30000),
        bill("wages_bill", date(2026, 10, 26), 30000, category="6100"),
        bill("essential", date(2026, 10, 26), 30000, essential=True),
        bill("too_late", date(2026, 11, 20), 30000),
    ]
    return base.model_copy(update={"open_bills": bills})


def test_three_levers_in_order() -> None:
    """T-PLN-08: COLLECT closes part, DELAY closes part, BORROW gets the rest."""
    inputs = _three_lever_inputs()
    plan = run(inputs)
    assert [s.lever for s in plan.steps] == [Lever.COLLECT, Lever.DELAY, Lever.BORROW]  # type: ignore[attr-defined]
    collect, delay, borrow = plan.steps  # type: ignore[attr-defined]
    assert collect.gap_after_cents == delay.gap_before_cents
    assert delay.closed_cents > 0 and delay.item_refs == ["delay_me"]
    assert borrow.gap_before_cents == plan.remaining_gap_cents > 0  # type: ignore[attr-defined]
    req = plan.financing_request  # type: ignore[attr-defined]
    assert req.amount_cents == plan.remaining_gap_cents  # type: ignore[attr-defined]
    assert 14 <= req.term_days <= 91 and req.avg_daily_card_sales_cents == 25000
    assert req.supplier_terms_available
    delay_draft = [d for d in plan.drafts if d.action_type == ActionType.DRAFT_DELAY_REQUEST]  # type: ignore[attr-defined]
    assert delay_draft[0].params == {"bill_id": "delay_me", "proposed_date": "2026-11-10"}
    assert all(d.action_type.name.startswith("DRAFT_") for d in plan.drafts)  # type: ignore[attr-defined]


def test_essential_never_delayed() -> None:
    """T-PLN-08 / PLN-08: wages, rent, loans, essential, penalised, no-grace and bills due
    after week 4 are never delay candidates."""
    inputs = _three_lever_inputs()
    rent = bill("rent", date(2026, 10, 25), 1000, category="6000")
    loan = bill("loan", date(2026, 10, 25), 1000, category="2500")
    inputs = inputs.model_copy(update={"open_bills": [*inputs.open_bills, rent, loan]})
    assert delayable(inputs) == ["delay_me"]


def test_recurring_wages_not_touched() -> None:
    """T-PLN-08: recurring obligations are not bills and never move."""
    inputs = _three_lever_inputs()
    wages = RecurringObligation(
        name="Payroll",
        category="6100",
        amount_cents=10000,
        first_date=date(2026, 10, 22),
        schedule="every_n_days",
        day_of_month=None,
        every_days=14,
        end_date=None,
        essential=True,
    )
    inputs = inputs.model_copy(update={"recurring": [*inputs.recurring, wages]})
    plan = run(inputs)
    refs = [r for s in plan.steps for r in s.item_refs]  # type: ignore[attr-defined]
    assert "Payroll" not in refs
