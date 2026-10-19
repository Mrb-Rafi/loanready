"""13-week weekly cash engine (PRD/06 §3; PLN-01, PLN-02).

For week k = 1..13 (``contracts.clock.week_range``) and each scenario:
  sales_k       = scenario weekly sales x product of the factors of every event that
                  overlaps week k by at least one day
  cogs_k        = sales_k x cogs_ratio (ratio clamped to [0, 0.95])
  collections_k = outstanding of invoices whose scenario collection date is in week k
  bills_k       = outstanding of open bills due in week k (already due -> week 1)
  recurring     = occurrences in week k, bucketed 6000 rent, 6100 wages, 6200 utilities,
                  2500/7000 loan, other
  closing_k     = opening_k + sales_k + collections_k - cogs_k - bills_k - rent_k - wages_k
                  - utilities_k - loan_k - other_k;  opening_{k+1} = closing_k
Every component is rounded to cents (``to_cents``) before it is summed, so each row adds
up exactly as displayed. Sign convention: inflows positive, outflows reported positive and
subtracted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from contracts.clock import HORIZON_WEEKS, week_of, week_range
from contracts.dto_books import RecurringObligation
from contracts.dto_planning import EventUplift, ForecastInputs, ScenarioForecast, WeekRow
from contracts.enums import Scenario
from contracts.money import fmt, to_cents
from planning.scenarios import clamp_cogs_ratio, collection_date, scenario_sales_cents

BUCKETS = {"6000": "rent", "6100": "wages", "6200": "utilities", "2500": "loan", "7000": "loan"}


def recurring_dates(r: RecurringObligation, start: date, end: date) -> list[date]:
    """``monthly``: day_of_month each month (clamped to the month length) from first_date;
    ``every_n_days``: first_date + n x every_days. Only dates in [start, end]."""
    out: list[date] = []
    if r.schedule == "monthly" and r.day_of_month:
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            d = date(y, m, min(r.day_of_month, (nxt - timedelta(days=1)).day))
            if start <= d <= end and d >= r.first_date and (r.end_date is None or d <= r.end_date):
                out.append(d)
            y, m = nxt.year, nxt.month
    elif r.schedule == "every_n_days" and r.every_days:
        d = r.first_date
        while d <= end:
            if d >= start and (r.end_date is None or d <= r.end_date):
                out.append(d)
            d += timedelta(days=r.every_days)
    return out


def uplift(events: list[EventUplift], start: date, end: date) -> Decimal:
    factor = Decimal(1)
    for ev in events:
        if ev.start <= end and ev.end >= start:
            factor *= ev.factor
    return factor


def bill_week(due: date, as_of: date) -> int | None:
    return 1 if due <= as_of else week_of(due, as_of)


@dataclass(frozen=True)
class Occurrence:
    """One dated outflow item, used by the drivers."""

    kind: str  # "bill" | "recurring"
    ref_id: str
    label: str
    amount_cents: int
    week: int
    when: date


def recurring_occurrences(inputs: ForecastInputs) -> list[Occurrence]:
    start, _ = week_range(1, inputs.as_of)
    _, end = week_range(HORIZON_WEEKS, inputs.as_of)
    out = []
    for r in inputs.recurring:
        for d in recurring_dates(r, start, end):
            k = week_of(d, inputs.as_of)
            if k is not None:
                out.append(Occurrence("recurring", r.name, r.name, r.amount_cents, k, d))
    return out


def run_scenario(inputs: ForecastInputs, scenario: Scenario) -> ScenarioForecast:
    as_of = inputs.as_of
    weekly = scenario_sales_cents([w.total_cents for w in inputs.weekly_sales], scenario)
    base_units = Decimal(weekly) / Decimal(100)
    ratio, clamped = clamp_cogs_ratio(inputs.cogs_ratio)
    collections: dict[int, int] = {}
    assumptions = [
        f"{scenario.value.title()} weekly sales {fmt(weekly)} "
        + {
            Scenario.LOW: "= 25th percentile of the last 12 weeks",
            Scenario.BASE: "= median of the last 8 weeks",
            Scenario.HIGH: "= 75th percentile of the last 12 weeks",
        }[scenario],
        f"Cash cost of goods {ratio} of sales" + (" (ratio clamped to 0..0.95)" if clamped else ""),
        "Card sales assumed to settle in the same week (settlement lag of at most 1 day)",
    ]
    for inv in sorted(inputs.open_invoices, key=lambda i: i.invoice_id):
        when = collection_date(inv, scenario, as_of)
        k = week_of(when, as_of) if when is not None else None
        if when is None:
            assumptions.append(f"Invoice {inv.invoice_id} assumed not collected (over 60 days)")
        else:
            assumptions.append(f"Invoice {inv.invoice_id} assumed paid {when.isoformat()}")
        if k is not None:
            collections[k] = collections.get(k, 0) + inv.outstanding_cents
    bills: dict[int, int] = {}
    for b in inputs.open_bills:
        k = bill_week(b.due_date, as_of)
        if k is not None:
            bills[k] = bills.get(k, 0) + b.outstanding_cents
    recurring: dict[tuple[int, str], int] = {}
    for r in inputs.recurring:
        bucket = BUCKETS.get(r.category, "other")
        for k in range(1, HORIZON_WEEKS + 1):
            start, end = week_range(k, as_of)
            n = len(recurring_dates(r, start, end))
            if n:
                recurring[(k, bucket)] = recurring.get((k, bucket), 0) + n * r.amount_cents
    rows: list[WeekRow] = []
    opening = inputs.opening_cash_cents
    for k in range(1, HORIZON_WEEKS + 1):
        start, end = week_range(k, as_of)
        sales_units = base_units * uplift(inputs.events, start, end)
        sales = to_cents(sales_units)
        cogs = to_cents(sales_units * ratio)
        coll = collections.get(k, 0)
        bill = bills.get(k, 0)
        rec = {b: recurring.get((k, b), 0) for b in ("rent", "wages", "utilities", "loan", "other")}
        net = sales + coll - cogs - bill - sum(rec.values())
        rows.append(
            WeekRow(
                week=k,
                start=start,
                end=end,
                opening_cents=opening,
                in_sales_cents=sales,
                in_collections_cents=coll,
                out_cogs_cents=cogs,
                out_bills_cents=bill,
                out_rent_cents=rec["rent"],
                out_wages_cents=rec["wages"],
                out_utilities_cents=rec["utilities"],
                out_loan_cents=rec["loan"],
                out_other_cents=rec["other"],
                net_cents=net,
                closing_cents=opening + net,
            )
        )
        opening += net
    first_negative = next((r.week for r in rows if r.closing_cents < 0), None)
    low = min(rows, key=lambda r: (r.closing_cents, r.week))
    for ev in inputs.events:
        assumptions.append(f"Event '{ev.name}' {ev.start}..{ev.end}: sales x {ev.factor}")
    return ScenarioForecast(
        scenario=scenario,
        weeks=rows,
        first_negative_week=first_negative,
        min_closing_cents=low.closing_cents,
        min_closing_week=low.week,
        assumptions=assumptions,
    )
