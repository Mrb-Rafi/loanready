"""Bank feed streams of the operating account (PRD/10 §3).

Every stream returns ``Flow`` rows (signed cents: + money in, - money out). ``balance.py``
adds equipment purchases where needed, orders the rows, numbers them and computes
``balance_after_cents``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from contracts.money import from_cents, to_cents
from datagen.params import ScenarioParams

# Ordering inside a day: inflows first (rank), then outflows (rank); see balance.order_key.
RANK_SETTLEMENT = 10
RANK_DEPOSIT = 11
RANK_CUSTOMER = 12
RANK_OTHER_IN = 13
RANK_RECURRING = 20
RANK_BILL = 21
RANK_PURCHASE = 22
RANK_OTHER_OUT = 23
RANK_EQUIPMENT = 99


@dataclass(frozen=True)
class Flow:
    day: date
    amount_cents: int  # signed: + in, - out
    description: str
    counterparty: str | None
    rank: int
    tag: str | None = None  # scenario / document marker, mapped to the txn id later


def days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def card_settlements(
    totals: dict[date, dict[str, int]], params: ScenarioParams, start: date, end: date
) -> list[Flow]:
    """Next-day inflow per sales day: net = card gross - fee, fee = gross x card_fee_rate
    (HALF_EVEN to cents). Gross and fee are in the description so WP3 can post row 3."""
    flows = []
    for d in days(start, end):
        gross = totals.get(d, {}).get("card", 0)
        settle = d + timedelta(days=1)
        if gross <= 0 or settle > end:
            continue
        fee = to_cents(from_cents(gross) * params.card_fee_rate)
        flows.append(
            Flow(
                day=settle,
                amount_cents=gross - fee,
                description=(
                    f"CARD SETTLEMENT {d.isoformat()} GROSS {from_cents(gross)} "
                    f"FEE {from_cents(fee)}"
                ),
                counterparty="Card acquirer",
                rank=RANK_SETTLEMENT,
            )
        )
    return flows


def cash_deposits(
    totals: dict[date, dict[str, int]], params: ScenarioParams, start: date, end: date
) -> list[Flow]:
    """Every Monday (end of day): deposit = cash sales since the previous deposit, Monday
    included. The last deposit is set so that the till closes at ``closing_till_cents``:
    till_end = opening_till + sum(cash sales) - sum(deposits)."""
    mondays = [d for d in days(start, end) if d.weekday() == 0]
    flows: list[Flow] = []
    pending = 0
    cursor = start
    for monday in mondays:
        for d in days(cursor, monday):
            pending += totals.get(d, {}).get("cash", 0)
        cursor = monday + timedelta(days=1)
        if pending > 0:
            flows.append(Flow(monday, pending, "CASH DEPOSIT", None, RANK_DEPOSIT))
            pending = 0
    if not flows:
        return flows
    all_cash = sum(totals.get(d, {}).get("cash", 0) for d in days(start, end))
    before_last = sum(f.amount_cents for f in flows[:-1])
    last = params.opening_till_cents + all_cash - before_last - params.closing_till_cents
    if last <= 0:
        raise ValueError("till target cannot be met with the last cash deposit")
    flows[-1] = Flow(flows[-1].day, last, "CASH DEPOSIT", None, RANK_DEPOSIT)
    return flows


def cash_cogs(
    rng: random.Random,
    totals: dict[date, dict[str, int]],
    params: ScenarioParams,
    start: date,
    end: date,
) -> list[Flow]:
    """Tuesday (covers Fri-Mon) and Friday (covers Tue-Thu) purchases:
    amount = covered POS sales x cash_cogs_ratio x U(1 - noise, 1 + noise)."""
    flows = []
    span = params.cash_cogs_noise_pct * 10
    for d in days(start, end):
        if d.weekday() not in (1, 4):
            continue
        covered = 4 if d.weekday() == 1 else 3
        sales = sum(
            totals.get(d - timedelta(days=k), {}).get("cash", 0)
            + totals.get(d - timedelta(days=k), {}).get("card", 0)
            for k in range(1, covered + 1)
        )
        if sales <= 0:
            continue
        noise = Decimal(1000 + rng.randint(-span, span)) / Decimal(1000)
        amount = to_cents(from_cents(sales) * params.cash_cogs_ratio * noise)
        flows.append(
            Flow(d, -amount, "GRAIN DEPOT WHOLESALE", "Grain Depot", RANK_PURCHASE, "cash_cogs")
        )
    return flows


def recurring(params: ScenarioParams, start: date, end: date) -> list[Flow]:
    """Rent (1st), wages (every 14 days from wages_first), utilities (20th), bank fee (28th)."""
    flows = []
    for d in days(start, end):
        if d.day == 1:
            flows.append(
                Flow(
                    d,
                    -params.rent_cents,
                    "RENT - MAPLE PROPERTIES",
                    "Maple Properties",
                    RANK_RECURRING,
                    "rent",
                )
            )
        if d >= params.wages_first and (d - params.wages_first).days % params.wages_every_days == 0:
            flows.append(Flow(d, -params.wages_cents, "PAYROLL", None, RANK_RECURRING, "wages"))
        if d.day == params.utilities_day:
            flows.append(
                Flow(d, -params.power_cents, "CITY POWER CO", "City Power Co", RANK_RECURRING)
            )
            flows.append(
                Flow(d, -params.internet_cents, "NETLINK INTERNET", "Netlink", RANK_RECURRING)
            )
        if d.day == params.bank_fee_day:
            flows.append(
                Flow(d, -params.bank_fee_cents, "BANK SERVICE CHARGE", None, RANK_OTHER_OUT)
            )
    return flows
