"""Financial statements from the journal (PRD/05 §7, BKS-16..BKS-19; REG-ACC-06, REG-ACC-10).

Sign conventions: income accounts report credit - debit, expense accounts debit - credit,
assets debit - credit, liabilities and equity credit - debit. Cash-flow lines are signed
(+ cash in, - cash out). Cash = accounts 1000 + 1010. OPENING entries are the starting
position, not flows: they are always inside the opening cash and never on a cash-flow line.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from books.policies import labels
from contracts.clock import month_key
from contracts.dto_books import (
    BalanceSheetDTO,
    CashFlowStatementDTO,
    JournalEntryDTO,
    ProfitAndLossDTO,
    StatementLine,
)
from contracts.enums import EntrySource, Framework
from contracts.registry import CASH_ACCOUNTS, CHART_OF_ACCOUNTS

NAMES = {a.code: a.name for a in CHART_OF_ACCOUNTS}
OPEX = ("6000", "6100", "6200", "6300", "6400", "6500", "6900")
ASSETS = ("1000", "1010", "1020", "1100", "1110", "1150", "1200", "1500")
LIABILITIES = ("2000", "2100", "2500")
INCOME = ("4000",)
EXPENSES = ("5000", *OPEX, "7000")
TOTAL = "total"


def months(start: date, end: date) -> list[str]:
    out: list[str] = []
    d = date(start.year, start.month, 1)
    while d <= end:
        out.append(month_key(d))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return out


def balances(entries: Iterable[JournalEntryDTO], end: date | None = None) -> dict[str, int]:
    """debit - credit per account over every entry dated <= end (OPENING always in)."""
    out: dict[str, int] = {}
    for e in entries:
        if end is not None and e.date > end and e.source != EntrySource.OPENING:
            continue
        for ln in e.lines:
            out[ln.account_code] = out.get(ln.account_code, 0) + ln.debit_cents - ln.credit_cents
    return out


def cash_balance(entries: Iterable[JournalEntryDTO], end: date) -> int:
    b = balances(entries, end)
    return sum(b.get(c, 0) for c in CASH_ACCOUNTS)


def profit_to_date(entries: Iterable[JournalEntryDTO], end: date) -> int:
    """Income - expenses over every entry dated <= end."""
    b = balances(entries, end)
    return -sum(b.get(c, 0) for c in INCOME) - sum(b.get(c, 0) for c in EXPENSES)


# --- P&L -----------------------------------------------------------------------------


def profit_and_loss(
    entries: list[JournalEntryDTO], start: date, end: date, framework: Framework
) -> ProfitAndLossDTO:
    """BKS-16: monthly columns plus a total. Gross profit = revenue - COGS; operating
    profit = gross profit - operating expenses (6000..6900); net profit = operating
    profit - interest (7000)."""
    periods = months(start, end)
    per: dict[str, dict[str, int]] = {p: {} for p in periods}
    for e in entries:
        if not start <= e.date <= end:
            continue
        bucket = per[month_key(e.date)]
        for ln in e.lines:
            bucket[ln.account_code] = (
                bucket.get(ln.account_code, 0) + ln.debit_cents - ln.credit_cents
            )

    def col(fn: object) -> dict[str, int]:
        vals = {p: fn(per[p]) for p in periods}  # type: ignore[operator]
        vals[TOTAL] = sum(vals.values())
        return vals

    def rev(b: dict[str, int]) -> int:
        return -sum(b.get(c, 0) for c in INCOME)

    def cogs(b: dict[str, int]) -> int:
        return b.get("5000", 0)

    def opex(b: dict[str, int]) -> int:
        return sum(b.get(c, 0) for c in OPEX)

    def interest(b: dict[str, int]) -> int:
        return b.get("7000", 0)

    lab = labels(framework)
    lines = [
        StatementLine(key="revenue", label="Revenue", values_cents=col(rev), level=1),
        StatementLine(key="cogs", label="Cost of goods sold", values_cents=col(cogs), level=1),
        StatementLine(
            key="gross_profit",
            label="Gross profit",
            values_cents=col(lambda b: rev(b) - cogs(b)),
            level=2,
        ),
    ]
    for code in OPEX:
        name = lab["impairment_expense"] if code == "6500" else NAMES[code]
        lines.append(
            StatementLine(
                key=f"opex_{code}",
                label=name,
                values_cents=col(lambda b, c=code: b.get(c, 0)),
                level=1,
            )
        )
    lines += [
        StatementLine(
            key="operating_profit",
            label="Operating profit",
            values_cents=col(lambda b: rev(b) - cogs(b) - opex(b)),
            level=2,
        ),
        StatementLine(
            key="interest", label="Interest expense", values_cents=col(interest), level=1
        ),
    ]
    net = col(lambda b: rev(b) - cogs(b) - opex(b) - interest(b))
    lines.append(StatementLine(key="net_profit", label="Net profit", values_cents=net, level=3))
    return ProfitAndLossDTO(
        framework=framework,
        periods=periods,
        lines=lines,
        net_profit_cents={p: net[p] for p in periods},
        total_net_profit_cents=net[TOTAL],
    )


# --- cash flow (direct) --------------------------------------------------------------

OPERATING, INVESTING, FINANCING = "operating", "investing", "financing"
_CF_LINES: tuple[tuple[str, str, str], ...] = (
    (OPERATING, "receipts_customers", "Cash received from customers"),
    (OPERATING, "paid_suppliers", "Cash paid to suppliers"),
    (OPERATING, "paid_employees", "Cash paid to employees"),
    (OPERATING, "rent_paid", "Rent paid"),
    (OPERATING, "other_operating", "Other operating payments"),
    (OPERATING, "interest_paid_op", "Interest paid"),
    (INVESTING, "equipment", "Purchase of equipment"),
    (FINANCING, "loans", "Loan proceeds / repayments"),
    (FINANCING, "interest_paid", "Interest paid"),
    (FINANCING, "capital", "Owner capital introduced"),
    (FINANCING, "drawings", "Owner's drawings"),
)
_SECTION = {key: section for section, key, _ in _CF_LINES}


def cf_key(code: str, framework: Framework) -> str:
    """Line for the other side of a cash movement (BKS-17 table). Interest paid is
    financing under IFRS for SMEs (policy choice, 7.14) and operating under US GAAP
    (ASC 230-10-45-17)."""
    if code in ("4000", "1100", "1020"):
        return "receipts_customers"
    if code in ("5000", "2000", "1200"):
        return "paid_suppliers"
    if code == "6100":
        return "paid_employees"
    if code == "6000":
        return "rent_paid"
    if code == "7000":
        return "interest_paid" if framework == Framework.IFRS_SME else "interest_paid_op"
    if code == "1500":
        return "equipment"
    if code == "2500":
        return "loans"
    if code in ("3000", "3200"):
        return "capital"
    if code == "3100":
        return "drawings"
    return "other_operating"


def _allocate(total: int, weights: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Split ``total`` over the codes pro rata to their weights (integer cents; the
    remainder goes to the largest weight, ties by code)."""
    if len(weights) == 1:
        return [(weights[0][0], total)]
    wsum = sum(w for _, w in weights)
    parts = [(c, total * w // wsum) for c, w in weights]
    rest = total - sum(p for _, p in parts)
    top = max(range(len(parts)), key=lambda i: (weights[i][1], weights[i][0]))
    parts[top] = (parts[top][0], parts[top][1] + rest)
    return parts


def entry_cash_flows(e: JournalEntryDTO, framework: Framework) -> list[tuple[str, int]]:
    """(line key, signed cents) for one entry; [] for internal 1000 <-> 1010 transfers."""
    delta = sum(
        ln.debit_cents - ln.credit_cents for ln in e.lines if ln.account_code in CASH_ACCOUNTS
    )
    if delta == 0:
        return []
    other = [ln for ln in e.lines if ln.account_code not in CASH_ACCOUNTS]
    side = [ln for ln in other if (ln.credit_cents if delta > 0 else ln.debit_cents) > 0]
    pool = side or other
    weights = sorted((ln.account_code, ln.credit_cents + ln.debit_cents) for ln in pool)
    return [(cf_key(code, framework), cents) for code, cents in _allocate(delta, weights)]


def cash_flow_statement(
    entries: list[JournalEntryDTO], start: date, end: date, framework: Framework
) -> CashFlowStatementDTO:
    """BKS-17, direct method. Opening = cash at start - 1 day; closing = opening + net
    operating + net investing + net financing (= ledger cash at end, identity 4)."""
    periods = months(start, end)
    vals: dict[str, dict[str, int]] = {key: dict.fromkeys(periods, 0) for _, key, _ in _CF_LINES}
    for e in entries:
        if e.source == EntrySource.OPENING or not start <= e.date <= end:
            continue
        for key, cents in entry_cash_flows(e, framework):
            vals[key][month_key(e.date)] += cents
    for v in vals.values():
        v[TOTAL] = sum(v[p] for p in periods)

    def section_total(section: str) -> dict[str, int]:
        keys = [k for s, k, _ in _CF_LINES if s == section]
        return {p: sum(vals[k][p] for k in keys) for p in [*periods, TOTAL]}

    titles = {
        OPERATING: "Operating activities",
        INVESTING: "Investing activities",
        FINANCING: "Financing activities",
    }
    lines: list[StatementLine] = []
    nets: dict[str, dict[str, int]] = {}
    for section in (OPERATING, INVESTING, FINANCING):
        lines.append(StatementLine(key=section, label=titles[section], values_cents={}, level=0))
        for s, key, label in _CF_LINES:
            if s != section:
                continue
            if key == "interest_paid_op" and framework == Framework.IFRS_SME:
                continue
            if key == "interest_paid" and framework != Framework.IFRS_SME:
                continue
            lines.append(StatementLine(key=key, label=label, values_cents=vals[key], level=1))
        nets[section] = section_total(section)
        lines.append(
            StatementLine(
                key=f"net_{section}",
                label=f"Net cash from {section} activities",
                values_cents=nets[section],
                level=2,
            )
        )
    opening = cash_balance(entries, start - timedelta(days=1))
    net_total = sum(nets[s][TOTAL] for s in nets)
    lines.append(
        StatementLine(
            key="net_change",
            label="Net change in cash",
            values_cents={p: sum(nets[s][p] for s in nets) for p in [*periods, TOTAL]},
            level=3,
        )
    )
    return CashFlowStatementDTO(
        framework=framework,
        method="direct",
        start=start,
        end=end,
        opening_cash_cents=opening,
        lines=lines,
        net_operating_cents=nets[OPERATING][TOTAL],
        net_investing_cents=nets[INVESTING][TOTAL],
        net_financing_cents=nets[FINANCING][TOTAL],
        closing_cash_cents=opening + net_total,
    )


def operating_cf(entries: list[JournalEntryDTO], start: date, end: date) -> int:
    """Net operating cash flow (direct, IFRS classification) over [start, end]."""
    total = 0
    for e in entries:
        if e.source == EntrySource.OPENING or not start <= e.date <= end:
            continue
        total += sum(
            c for k, c in entry_cash_flows(e, Framework.IFRS_SME) if _SECTION[k] == OPERATING
        )
    return total


# --- balance sheet -------------------------------------------------------------------


def balance_sheet(
    entries: list[JournalEntryDTO], as_of: date, framework: Framework
) -> BalanceSheetDTO:
    """BKS-18: assets (1150 shown negative), liabilities, equity = capital - drawings +
    retained earnings + profit for the period to date. No closing entries are posted."""
    b = balances(entries, as_of)
    key = as_of.isoformat()
    lab = labels(framework)
    lines: list[StatementLine] = [
        StatementLine(key="assets", label="Assets", values_cents={}, level=0)
    ]
    for code in ASSETS:
        name = lab["allowance"] if code == "1150" else NAMES[code]
        lines.append(
            StatementLine(key=code, label=name, values_cents={key: b.get(code, 0)}, level=1)
        )
    assets = sum(b.get(c, 0) for c in ASSETS)
    lines.append(
        StatementLine(key="total_assets", label="Total assets", values_cents={key: assets}, level=3)
    )
    lines.append(StatementLine(key="liabilities", label="Liabilities", values_cents={}, level=0))
    for code in LIABILITIES:
        lines.append(
            StatementLine(key=code, label=NAMES[code], values_cents={key: -b.get(code, 0)}, level=1)
        )
    liabilities = -sum(b.get(c, 0) for c in LIABILITIES)
    lines.append(
        StatementLine(
            key="total_liabilities",
            label="Total liabilities",
            values_cents={key: liabilities},
            level=3,
        )
    )
    profit = profit_to_date(entries, as_of)
    equity_parts = [
        ("3000", NAMES["3000"], -b.get("3000", 0)),
        ("3100", NAMES["3100"], -b.get("3100", 0)),
        ("3200", NAMES["3200"], -b.get("3200", 0)),
        ("profit_to_date", "Profit for the period to date", profit),
    ]
    lines.append(StatementLine(key="equity", label="Equity", values_cents={}, level=0))
    for k, label, cents in equity_parts:
        lines.append(StatementLine(key=k, label=label, values_cents={key: cents}, level=1))
    equity = sum(c for _, _, c in equity_parts)
    lines.append(
        StatementLine(key="total_equity", label="Total equity", values_cents={key: equity}, level=3)
    )
    return BalanceSheetDTO(
        framework=framework,
        as_of=as_of,
        lines=lines,
        total_assets_cents=assets,
        total_liabilities_cents=liabilities,
        total_equity_cents=equity,
    )
