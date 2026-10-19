"""Independent expected values (``data/expected.json``) and the minimal BASE forecast used
by the tuning loop (PRD/10 §5, §7).

The forecast here is a deliberately simple re-implementation of the PRD/06 BASE rules,
computed straight from the seed bundle (it does not import WP3 or WP4):

- opening cash = bank closing + till closing (BKS-31: balance of 1000 + 1010);
- weekly sales = median of the last 8 POS weeks (BKS-29 windows), x event uplift of every
  event overlapping the week;
- COGS = sales x cash-COGS ratio, ratio = GRAIN DEPOT outflows / POS sales over the last
  12 weeks (BKS-30: cash-paid purchases only), 4 dp;
- collections: not yet due -> due + median lateness (no history: 14 days); overdue ->
  later of AS_OF + 14 and due + median lateness; more than 60 days overdue -> excluded;
- open bills on their due date (week 1 if already due), suspected fraud excluded (claimed
  account differs from the payee's stored account, ORC-07);
- recurring obligations on schedule.
Every component is rounded to cents before it is summed (PLN step 7). Sign convention:
inflows positive, outflows subtracted; ``closing_k = opening_k + in - out``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from contracts.clock import HORIZON_WEEKS, week_of, week_range
from contracts.dto_books import BillDTO, RecurringObligation
from contracts.enums import BillStatus, InvoiceStatus
from contracts.money import from_cents, to_cents
from contracts.seed import SeedBundle

DELAY_EXCLUDED_CATEGORIES = frozenset({"6000", "6100", "2500", "7000"})


def percentile(values: list[Decimal], p: int) -> Decimal:
    """Linear interpolation (numpy "linear"): h = (n-1)p/100,
    x[floor h] + (h - floor h)(x[floor h + 1] - x[floor h])."""
    xs = sorted(values)
    if not xs:
        raise ValueError("percentile of empty list")
    h = Decimal(len(xs) - 1) * Decimal(p) / Decimal(100)
    lo = int(h.to_integral_value(rounding=ROUND_FLOOR))
    if lo + 1 >= len(xs):
        return xs[lo]
    return xs[lo] + (h - lo) * (xs[lo + 1] - xs[lo])


def weekly_pos_totals(bundle: SeedBundle, weeks: int) -> list[int]:
    """POS totals of week j = [as_of - 7j + 1, as_of - 7(j-1)], oldest first (BKS-29)."""
    per_day: dict[date, int] = {}
    for s in bundle.pos_sales:
        per_day[s.ts.date()] = per_day.get(s.ts.date(), 0) + s.amount_cents
    out = []
    for j in range(weeks, 0, -1):
        start = bundle.as_of - timedelta(days=7 * j - 1)
        out.append(sum(per_day.get(start + timedelta(days=k), 0) for k in range(7)))
    return out


def cash_cogs_ratio(bundle: SeedBundle, weeks: int = 12) -> Decimal:
    """GRAIN DEPOT outflows / POS sales over the last ``weeks`` weeks, 4 dp."""
    start = bundle.as_of - timedelta(days=7 * weeks - 1)
    cogs = -sum(
        t.amount_cents
        for t in bundle.bank_transactions
        if start <= t.date <= bundle.as_of and t.description.startswith("GRAIN DEPOT")
    )
    sales = sum(weekly_pos_totals(bundle, weeks))
    return (Decimal(cogs) / Decimal(sales)).quantize(Decimal("0.0001"))


def till_closing_cents(bundle: SeedBundle) -> int:
    """Opening till + cash sales - cash deposits."""
    opening = sum(
        line.debit_cents - line.credit_cents
        for line in bundle.opening_balances
        if line.account_code == "1010"
    )
    cash = sum(s.amount_cents for s in bundle.pos_sales if s.tender == "cash")
    deposits = sum(
        t.amount_cents for t in bundle.bank_transactions if t.description == "CASH DEPOSIT"
    )
    return opening + cash - deposits


def suspected_fraud(bundle: SeedBundle, bill: BillDTO) -> bool:
    """claimed_bank_account_ref set and different from the payee's stored account."""
    if bill.claimed_bank_account_ref is None:
        return False
    stored = {p.id: p.bank_account_ref for p in bundle.payees}.get(bill.payee_id or "")
    return bill.claimed_bank_account_ref != stored


def _recurring_dates(r: RecurringObligation, start: date, end: date) -> list[date]:
    out = []
    if r.schedule == "monthly" and r.day_of_month:
        y, m = start.year, start.month
        while date(y, m, 1) <= end:
            nxt = date(y + (m == 12), m % 12 + 1, 1)
            last_day = (nxt - timedelta(days=1)).day
            d = date(y, m, min(r.day_of_month, last_day))
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


def _base_collection(bundle: SeedBundle, customer_id: str, due: date) -> date | None:
    hist = next(c.lateness_history_days for c in bundle.counterparties if c.id == customer_id)
    late_dec = percentile([Decimal(max(0, d)) for d in hist], 50) if hist else Decimal(14)
    late = int(late_dec.to_integral_value(rounding=ROUND_FLOOR))
    if due < bundle.as_of:
        if (bundle.as_of - due).days > 60:
            return None
        return max(bundle.as_of + timedelta(days=14), due + timedelta(days=late))
    return due + timedelta(days=late)


def base_closings(
    bundle: SeedBundle,
    collect_overdue_on: date | None = None,
    bill_due_override: dict[str, date] | None = None,
) -> list[int]:
    """13 BASE closing balances (cents). ``collect_overdue_on`` moves every invoice overdue
    by at most 60 days to that date (COLLECT what-if); ``bill_due_override`` moves bills
    (DELAY what-if)."""
    as_of = bundle.as_of
    weekly = weekly_pos_totals(bundle, 8)
    # HALF_EVEN to whole cents through contracts.money, exactly like WP4 (an 8-week median
    # can end in .5 cent; int() would truncate it)
    base_sales = from_cents(to_cents(percentile([Decimal(w) for w in weekly], 50) / 100))
    ratio = cash_cogs_ratio(bundle)
    overrides = bill_due_override or {}
    collections: dict[int, int] = {}
    for inv in bundle.invoices:
        if inv.status not in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL):
            continue
        overdue_days = (as_of - inv.due_date).days
        if collect_overdue_on is not None and 0 < overdue_days <= 60:
            when: date | None = collect_overdue_on
        else:
            when = _base_collection(bundle, inv.customer_id, inv.due_date)
        k = week_of(when, as_of) if when is not None else None
        if k is not None:
            collections[k] = collections.get(k, 0) + inv.outstanding_cents
    bills: dict[int, int] = {}
    for b in bundle.bills:
        if b.status not in (BillStatus.OPEN, BillStatus.PARTIAL) or suspected_fraud(bundle, b):
            continue
        due = overrides.get(b.id, b.due_date)
        k = 1 if due <= as_of else week_of(due, as_of)
        if k is not None:
            bills[k] = bills.get(k, 0) + b.outstanding_cents
    closing = sum(a.balance_cents for a in bundle.bank_accounts) + till_closing_cents(bundle)
    out = []
    for k in range(1, HORIZON_WEEKS + 1):
        start, end = week_range(k, as_of)
        uplift = Decimal(1)
        for ev in bundle.events:
            if ev.start <= end and ev.end >= start:
                uplift *= ev.factor
        sales_dec = base_sales * uplift
        sales = to_cents(sales_dec)
        cogs = to_cents(sales_dec * ratio)
        fixed = sum(r.amount_cents * len(_recurring_dates(r, start, end)) for r in bundle.recurring)
        closing += sales + collections.get(k, 0) - cogs - bills.get(k, 0) - fixed
        out.append(closing)
    return out


def roundup_100(cents: int) -> int:
    """Round up to the next whole 100.00 (10,000 cents)."""
    return -(-cents // 10000) * 10000


def _gap(closings: list[int]) -> int:
    return roundup_100(max(0, -min(closings)))


def delay_overrides(bundle: SeedBundle) -> dict[str, date]:
    """Eligible bills (no penalty, not essential, not rent/wages/loan, grace > 0, due in
    weeks 1-4) moved to due + grace_days."""
    out = {}
    for b in bundle.bills:
        if b.status not in (BillStatus.OPEN, BillStatus.PARTIAL) or suspected_fraud(bundle, b):
            continue
        k = week_of(b.due_date, bundle.as_of)
        if (
            not b.late_penalty
            and not b.essential
            and b.category not in DELAY_EXCLUDED_CATEGORIES
            and b.grace_days > 0
            and k is not None
            and k <= 4
        ):
            out[b.id] = b.due_date + timedelta(days=b.grace_days)
    return out


def forecast_check(
    bundle: SeedBundle, margin_cents: int = 0, min_borrow_cents: int = 0
) -> dict[str, Any]:
    """S2 (W1, W2 >= 0, W3 < 0) and S4 (gap after collect > 0, gap after delay > 0, and
    the remaining BORROW need at least ``min_borrow_cents`` + margin, so the MCA is
    eligible). ``direction`` tells the tuning loop which way to move POS sales (-1 lower,
    +1 higher)."""
    base = base_closings(bundle)
    collect_on = bundle.as_of + timedelta(days=5)
    after_collect = base_closings(bundle, collect_overdue_on=collect_on)
    after_delay = base_closings(
        bundle, collect_overdue_on=collect_on, bill_due_override=delay_overrides(bundle)
    )
    first_negative = next((k + 1 for k, c in enumerate(base) if c < 0), None)
    s2 = base[0] >= 0 and base[1] >= 0 and base[2] < 0
    s4 = _gap(after_collect) > 0 and _gap(after_delay) > 0
    direction = 0
    if min(after_delay) > -(margin_cents + min_borrow_cents):
        direction = -1  # too much cash: the delay leaves too small a BORROW need
    elif min(base[0], base[1]) < margin_cents:
        direction = 1  # too little cash before week 3
    return {
        "base_closings_cents": base,
        "first_negative_week_base": first_negative,
        "gap_cents": _gap(base),
        "gap_after_collect_cents": _gap(after_collect),
        "gap_after_delay_cents": _gap(after_delay),
        "cogs_ratio": str(cash_cogs_ratio(bundle)),
        "s2_ok": s2,
        "s4_ok": s4,
        "direction": direction,
    }


def expected_values(
    bundle: SeedBundle, params_json: dict[str, Any], tuning: dict[str, Any]
) -> dict[str, Any]:
    """Contents of data/expected.json (PRD/10 §7)."""
    sid = bundle.scenario_ids
    by_id = {t.id: t for t in bundle.bank_transactions}
    bills = {b.id: b for b in bundle.bills}
    invoices = {i.id: i for i in bundle.invoices}
    check = forecast_check(bundle)
    return {
        "seed": bundle.seed,
        "as_of": bundle.as_of.isoformat(),
        "params": params_json,
        "S1": {
            "personal_ids": sid["S1"],
            "total_cents": -sum(by_id[i].amount_cents for i in sid["S1"]),
        },
        "S2": {
            "bill_id": sid["S2"][0],
            "first_negative_week_base": check["first_negative_week_base"],
        },
        "S3": {"overdue_invoice_ids": sid["S3"]},
        "S4": {
            "bill_id": sid["S4"][0],
            "cheapest_kind": "SUPPLIER_CREDIT",
            "most_expensive_id": "mca_swiftadvance",
            "gap_after_collect_cents": check["gap_after_collect_cents"],
            "gap_after_delay_cents": check["gap_after_delay_cents"],
        },
        "S5": {"duplicate_ids": sid["S5"]},
        "S6": {"bill_id": sid["S6"][0], "expected_reason": "ACCOUNT_CHANGED"},
        "S7": {"injection_refs": sid["S7"], "min_flags": 4},
        "S8": {"receipt": "receipt_blurry.png", "status": "NEEDS_CONFIRMATION"},
        "S9": {
            "invoice_id": sid["S9"][0],
            "bucket": "61-90",
            "days_overdue": (bundle.as_of - invoices[sid["S9"][0]].due_date).days,
            "ecl_cents": to_cents(
                from_cents(invoices[sid["S9"][0]].outstanding_cents) * Decimal("0.25")
            ),
        },
        "S10": {"bill_id": sid["S10"][0], "amount_cents": bills[sid["S10"][0]].amount_cents},
        "closing_bank_cents": sum(a.balance_cents for a in bundle.bank_accounts),
        "closing_till_cents": till_closing_cents(bundle),
        "forecast_check": check,
        "tuning": tuning,
    }
