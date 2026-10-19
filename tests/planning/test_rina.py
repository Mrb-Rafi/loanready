"""PRD/06 §9 acceptance on the seeded bakery (S2, S3, S4), with forecast inputs built from
the WP8 bundle the way the orchestrator does (ORC-07). Skips when WP8 is absent."""

from __future__ import annotations

import importlib.util
from datetime import date, timedelta
from typing import Any

import pytest

from contracts.enums import Lever, Scenario
from planning.service import Planner

pytestmark = pytest.mark.skipif(importlib.util.find_spec("datagen") is None, reason="WP8 absent")


@pytest.fixture(scope="module")
def rina() -> Any:
    from contracts.dto_books import WeeklySales
    from contracts.dto_planning import BillForecastItem, ForecastInputs, InvoiceForecastItem
    from datagen import expected
    from datagen.generate import tune

    b = tune().bundle
    as_of = b.as_of
    totals = expected.weekly_pos_totals(b, 12)
    weekly = [
        WeeklySales(
            week_start=as_of - timedelta(days=7 * (12 - i) - 6),
            cash_cents=0,
            card_sales_cents=t,
            total_cents=t,
        )
        for i, t in enumerate(totals)
    ]
    lateness = {c.id: c.lateness_history_days for c in b.counterparties}
    inputs = ForecastInputs(
        as_of=as_of,
        opening_cash_cents=b.bank_accounts[0].balance_cents + expected.till_closing_cents(b),
        weekly_sales=weekly,
        cogs_ratio=expected.cash_cogs_ratio(b),
        open_invoices=[
            InvoiceForecastItem(
                invoice_id=i.id,
                customer_id=i.customer_id,
                due_date=i.due_date,
                outstanding_cents=i.outstanding_cents,
                lateness_history_days=lateness[i.customer_id],
            )
            for i in b.invoices
            if i.status.value in ("OPEN", "PARTIAL")
        ],
        open_bills=[
            BillForecastItem(
                bill_id=x.id,
                supplier_id=x.supplier_id,
                due_date=x.due_date,
                outstanding_cents=x.outstanding_cents,
                category=x.category,
                late_penalty=x.late_penalty,
                essential=x.essential,
                grace_days=x.grace_days,
            )
            for x in b.bills
            if x.status.value in ("OPEN", "PARTIAL") and not expected.suspected_fraud(b, x)
        ],
        recurring=b.recurring,
        events=b.events,
    )
    return b, inputs


def test_s2_week_three(rina: Any) -> None:
    """S2: BASE first goes negative in week 3; drivers name the festival bill and the two
    overdue catering invoices; WP8's independent check agrees week by week."""
    from datagen.expected import forecast_check

    b, inputs = rina
    f = Planner().forecast(inputs)
    base = f.scenarios[Scenario.BASE]
    assert base.first_negative_week == 3
    assert [w.closing_cents for w in base.weeks] == forecast_check(b)["base_closings_cents"]
    assert f.drivers.top_outflows[0].ref_id == b.scenario_ids["S2"][0]
    delayed = {i.ref_id for i in f.drivers.delayed_inflows}
    # PRD/06 §4: every invoice due by the end of week 3 but expected later; that is the
    # two overdue catering invoices (S3) plus two cafe invoices due 10-26 and 10-31.
    assert set(b.scenario_ids["S3"]) <= delayed
    assert f.drivers.facts["delayed_count"] == str(len(delayed))


def test_s4_collect_delay_borrow(rina: Any) -> None:
    """S3/S4: COLLECT closes part (two reminders), DELAY moves BILL-2215, BORROW gets the
    positive remainder."""
    b, inputs = rina
    p = Planner()
    f = p.forecast(inputs)
    plan = p.plan(inputs, f, 20000)
    assert [s.lever for s in plan.steps] == [Lever.COLLECT, Lever.DELAY, Lever.BORROW]
    collect, delay, _ = plan.steps
    assert sorted(collect.item_refs) == sorted(b.scenario_ids["S3"])
    assert collect.closed_cents > 0 and delay.closed_cents > 0
    assert delay.item_refs == b.scenario_ids["S4"]
    assert plan.remaining_gap_cents > 0
    assert plan.financing_request.amount_cents == plan.remaining_gap_cents
    delay_draft = next(d for d in plan.drafts if d.params.get("bill_id"))
    assert (
        delay_draft.params["proposed_date"] == (date(2026, 10, 31) + timedelta(days=14)).isoformat()
    )
