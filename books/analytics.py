"""Analytics for the forecast and the readiness score (PRD/05 §12, BKS-29..BKS-32)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from books.repo import RawRow
from books.statements import months, operating_cf
from contracts.clock import month_key
from contracts.dto_books import InvoiceDTO, JournalEntryDTO, WeeklySales
from contracts.dto_financing import ReadinessInputs
from contracts.enums import EntrySource, InvoiceStatus, RawStatus, SourceKind
from contracts.money import to_cents
from contracts.registry import CASH_ACCOUNTS

FOUR_DP = Decimal("0.0001")


def week_bounds(j: int, as_of: date) -> tuple[date, date]:
    """Week j counted backwards: [as_of - 7j + 1, as_of - 7(j - 1)]."""
    return as_of - timedelta(days=7 * j - 1), as_of - timedelta(days=7 * (j - 1))


def weekly_sales(pos: list[RawRow], weeks: int, as_of: date) -> list[WeeklySales]:
    """BKS-29: POS sales only (cash + card) per week, oldest first."""
    out = []
    for j in range(weeks, 0, -1):
        start, end = week_bounds(j, as_of)
        rows = [r for r in pos if start <= r.date <= end and r.source == SourceKind.POS]
        cash = sum(r.amount_cents for r in rows if r.tender == "cash")
        total = sum(r.amount_cents for r in rows)
        out.append(
            WeeklySales(
                week_start=start, cash_cents=cash, card_sales_cents=total - cash, total_cents=total
            )
        )
    return out


def cash_cogs_cents(entries: list[JournalEntryDTO], start: date, end: date) -> int:
    """Debits to 5000 in entries that credit a cash account (rows 9 and 12): cash-paid
    COGS only. Credit purchases (bills, row 7) are already in open bills (BKS-30)."""
    total = 0
    for e in entries:
        if not start <= e.date <= end:
            continue
        if not any(ln.account_code in CASH_ACCOUNTS and ln.credit_cents > 0 for ln in e.lines):
            continue
        total += sum(ln.debit_cents for ln in e.lines if ln.account_code == "5000")
    return total


def cogs_ratio(
    entries: list[JournalEntryDTO], pos: list[RawRow], weeks: int, as_of: date
) -> Decimal:
    """cash COGS / POS sales over the last ``weeks`` weeks, 4 dp (0 when no sales)."""
    start = as_of - timedelta(days=7 * weeks - 1)
    sales = sum(r.amount_cents for r in pos if start <= r.date <= as_of)
    if sales <= 0:
        return Decimal("0.0000")
    return (Decimal(cash_cogs_cents(entries, start, as_of)) / Decimal(sales)).quantize(FOUR_DP)


def avg_cents(total: int, n: int) -> int:
    """total / n in cents, rounded HALF_EVEN through contracts.money."""
    return to_cents(Decimal(total) / Decimal(n) / Decimal(100))


def _share(num: int, den: int) -> Decimal:
    if den <= 0:
        return Decimal("1.0000")
    return (Decimal(num) / Decimal(den)).quantize(FOUR_DP)


def _last_full_months(as_of: date, n: int) -> list[str]:
    first_of_month = date(as_of.year, as_of.month, 1)
    keys: list[str] = []
    d = first_of_month - timedelta(days=1)
    while len(keys) < n:
        keys.append(month_key(d))
        d = date(d.year, d.month, 1) - timedelta(days=1)
    return list(reversed(keys))


def readiness_inputs(
    as_of: date,
    entries: list[JournalEntryDTO],
    raw: list[RawRow],
    invoices: list[InvoiceDTO],
    personal_total: int,
    personal_resolved: int,
    unconfirmed: int,
) -> ReadinessInputs:
    """BKS-32. Monthly figures are averages of the last 3 full calendar months."""
    per_month: dict[str, int] = {}
    for e in entries:
        if e.date <= as_of:
            per_month[month_key(e.date)] = per_month.get(month_key(e.date), 0) + 1
    months_of_records = sum(1 for n in per_month.values() if n >= 10)
    done = sum(r.status in (RawStatus.POSTED, RawStatus.CATEGORISED) for r in raw)
    bank90 = [
        r
        for r in raw
        if r.source == SourceKind.BANK and as_of - timedelta(days=90) < r.date <= as_of
    ]
    reconciled = sum(r.status == RawStatus.POSTED for r in bank90)
    pos = [r for r in raw if r.source == SourceKind.POS]
    weekly = [w.total_cents for w in weekly_sales(pos, 12, as_of)]
    weekly_cf = []
    for j in range(12, 0, -1):
        start, end = (as_of - timedelta(days=7 * j - 1), as_of - timedelta(days=7 * (j - 1)))
        weekly_cf.append(operating_cf(entries, start, end))
    open_inv = [i for i in invoices if i.status in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)]
    receivables = sum(i.outstanding_cents for i in open_inv)
    overdue = sum(i.outstanding_cents for i in open_inv if i.due_date < as_of)
    keys = _last_full_months(as_of, 3)
    sales_m = dict.fromkeys(keys, 0)
    debt_m = dict.fromkeys(keys, 0)
    for e in entries:
        k = month_key(e.date)
        if k not in sales_m:
            continue
        for ln in e.lines:
            if ln.account_code == "4000":
                sales_m[k] += ln.credit_cents - ln.debit_cents
        if e.source != EntrySource.OPENING and any(
            ln.account_code in CASH_ACCOUNTS and ln.credit_cents > 0 for ln in e.lines
        ):
            debt_m[k] += sum(
                ln.debit_cents for ln in e.lines if ln.account_code in ("2500", "7000")
            )
    first = date.fromisoformat(keys[0] + "-01")
    last = date.fromisoformat(keys[-1] + "-01")
    last_end = date(last.year + (last.month == 12), last.month % 12 + 1, 1) - timedelta(days=1)
    op_cf = operating_cf(entries, first, last_end)
    n = len(months(first, last_end))
    return ReadinessInputs(
        as_of=as_of,
        months_of_records=months_of_records,
        categorised_share=_share(done, len(raw)),
        reconciled_share=_share(reconciled, len(bank90)),
        personal_separated_share=_share(personal_resolved, personal_total),
        unconfirmed_count=unconfirmed,
        weekly_sales=weekly,
        weekly_operating_cf=weekly_cf,
        receivables_cents=receivables,
        overdue_receivables_cents=overdue,
        monthly_sales_cents=avg_cents(sum(sales_m.values()), len(keys)),
        monthly_debt_service_cents=avg_cents(sum(debt_m.values()), len(keys)),
        monthly_operating_cf_cents=avg_cents(op_cf, n),
    )
