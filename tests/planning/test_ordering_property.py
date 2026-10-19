"""T-PLN-03 proves PLN-01: LOW <= BASE <= HIGH closing in every week (hypothesis);
T-PLN-09 proves PLN-02: same inputs -> identical DTO and inputs_hash."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.dto_books import WeeklySales
from contracts.dto_planning import BillForecastItem, ForecastInputs, InvoiceForecastItem
from contracts.enums import Scenario
from contracts.testing.builders import make_forecast_inputs_tiny
from planning.service import Planner

AS_OF = date(2026, 10, 14)


@st.composite
def inputs(draw: st.DrawFn) -> ForecastInputs:
    weeks = draw(st.lists(st.integers(0, 500_000), min_size=1, max_size=14))
    invoices = [
        InvoiceForecastItem(
            invoice_id=f"i{n}",
            customer_id="c",
            due_date=AS_OF + timedelta(days=draw(st.integers(-90, 60))),
            outstanding_cents=draw(st.integers(1, 300_000)),
            lateness_history_days=draw(st.lists(st.integers(-10, 90), max_size=5)),
        )
        for n in range(draw(st.integers(0, 4)))
    ]
    bills = [
        BillForecastItem(
            bill_id=f"b{n}",
            supplier_id="s",
            due_date=AS_OF + timedelta(days=draw(st.integers(-10, 100))),
            outstanding_cents=draw(st.integers(1, 500_000)),
            category="5000",
            late_penalty=draw(st.booleans()),
            essential=False,
            grace_days=draw(st.integers(0, 20)),
        )
        for n in range(draw(st.integers(0, 4)))
    ]
    ratio = Decimal(draw(st.integers(0, 120))) / Decimal(100)
    return ForecastInputs(
        as_of=AS_OF,
        opening_cash_cents=draw(st.integers(-100_000, 1_000_000)),
        weekly_sales=[
            WeeklySales(
                week_start=AS_OF - timedelta(days=7 * (len(weeks) - i) - 6),
                cash_cents=w,
                card_sales_cents=0,
                total_cents=w,
            )
            for i, w in enumerate(weeks)
        ],
        cogs_ratio=ratio,
        open_invoices=invoices,
        open_bills=bills,
        recurring=[],
        events=[],
    )


@settings(max_examples=100, deadline=None, derandomize=True)
@given(inputs())
def test_low_base_high_ordering(i: ForecastInputs) -> None:
    """T-PLN-03: every week LOW <= BASE <= HIGH (COGS ratio > 0.95 is clamped)."""
    f = Planner().forecast(i)
    for lo, ba, hi in zip(
        f.scenarios[Scenario.LOW].weeks,
        f.scenarios[Scenario.BASE].weeks,
        f.scenarios[Scenario.HIGH].weeks,
        strict=True,
    ):
        assert lo.closing_cents <= ba.closing_cents <= hi.closing_cents, lo.week


def test_determinism() -> None:
    """T-PLN-09: two runs give equal DTOs and the same inputs_hash; the hash moves with
    the inputs."""
    a = Planner().forecast(make_forecast_inputs_tiny(crunch=True))
    b = Planner().forecast(make_forecast_inputs_tiny(crunch=True))
    assert a == b and a.inputs_hash == b.inputs_hash
    assert Planner().forecast(make_forecast_inputs_tiny()).inputs_hash != a.inputs_hash
