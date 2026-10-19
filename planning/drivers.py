"""Why cash dips: facts for the explanation sentence (PRD/06 §4, PLN-03).

For BASE (or LOW if BASE never goes negative): the crossing week, the 3 largest single
outflows in that week (bills, recurring occurrences, the cash COGS line), and invoices
expected after the crossing week although due by its end. The sentence itself is worded
by the agent (or its template) from ``facts`` only.

Inputs carry ids, not document numbers, so labels use ids (e.g. "bill bil_rina_2207 from
cpt_golden"); the orchestrator may map them to names for display.
"""

from __future__ import annotations

from datetime import date

from contracts.clock import HORIZON_WEEKS, week_of
from contracts.dto_planning import DriverItem, DriversDTO, ForecastInputs, ScenarioForecast
from contracts.enums import Scenario
from contracts.money import fmt
from planning.forecast import bill_week, recurring_occurrences
from planning.scenarios import collection_date

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day_label(d: date) -> str:
    """ "29 Oct 2026" (locale-independent)."""
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def drivers(inputs: ForecastInputs, scenarios: dict[Scenario, ScenarioForecast]) -> DriversDTO:
    scenario = Scenario.BASE
    sf = scenarios[Scenario.BASE]
    if sf.first_negative_week is None and scenarios[Scenario.LOW].first_negative_week is not None:
        scenario, sf = Scenario.LOW, scenarios[Scenario.LOW]
    k = sf.first_negative_week
    if k is None:
        return DriversDTO(
            scenario=scenario, crossing_week=None, top_outflows=[], delayed_inflows=[], facts={}
        )
    row = sf.weeks[k - 1]
    items: list[DriverItem] = []
    for b in inputs.open_bills:
        if bill_week(b.due_date, inputs.as_of) == k:
            items.append(
                DriverItem(
                    kind="outflow",
                    ref_id=b.bill_id,
                    label=f"bill {b.bill_id} from {b.supplier_id}",
                    amount_cents=b.outstanding_cents,
                    week=k,
                    expected_date=b.due_date,
                )
            )
    for occ in recurring_occurrences(inputs):
        if occ.week == k:
            items.append(
                DriverItem(
                    kind="outflow",
                    ref_id=occ.ref_id,
                    label=occ.label,
                    amount_cents=occ.amount_cents,
                    week=k,
                    expected_date=occ.when,
                )
            )
    if row.out_cogs_cents > 0:
        items.append(
            DriverItem(
                kind="outflow",
                ref_id="cogs",
                label="ingredient and packaging purchases",
                amount_cents=row.out_cogs_cents,
                week=k,
                expected_date=row.start,
            )
        )
    top = sorted(items, key=lambda i: (-i.amount_cents, i.ref_id))[:3]
    delayed: list[DriverItem] = []
    for inv in sorted(inputs.open_invoices, key=lambda i: i.invoice_id):
        when = collection_date(inv, scenario, inputs.as_of)
        if when is None or when <= row.end or inv.due_date > row.end:
            continue
        delayed.append(
            DriverItem(
                kind="delayed_inflow",
                ref_id=inv.invoice_id,
                label=f"invoice {inv.invoice_id}",
                amount_cents=inv.outstanding_cents,
                week=week_of(when, inputs.as_of) or HORIZON_WEEKS + 1,
                expected_date=when,
            )
        )
    facts = {
        "week": str(k),
        "week_start": day_label(row.start),
        "main_outflow": top[0].label if top else "",
        "main_outflow_amount": fmt(top[0].amount_cents) if top else fmt(0),
        "delayed_count": str(len(delayed)),
        "delayed_total": fmt(sum(d.amount_cents for d in delayed)),
    }
    return DriversDTO(
        scenario=scenario,
        crossing_week=k,
        top_outflows=top,
        delayed_inflows=delayed,
        facts=facts,
    )
