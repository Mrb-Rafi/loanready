"""One function per loan-pack section (PRD/08 §5). WP6 only renders: every number comes
from ``LoanPackContent`` (computed by books, planning, financing); here it is formatted.

Every text value passes ``safe()``: XML-escaped for reportlab and with account-like
numbers masked to the last 4 digits (OUT-08). Raw OCR text is never in the content.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, Spacer, Table

from contracts.dto_books import StatementLine
from contracts.dto_outputs import LoanPackContent
from contracts.enums import Framework, Scenario
from contracts.money import fmt, pct
from outputs.loanpack import styles as st

BANNER_TEXT = "SYNTHETIC DATA — PROTOTYPE — NOT A REAL APPLICATION"
_SBX = re.compile(r"\b(SBX-[A-Z]{2}-)\d*(\d{4})\b")
_LONG_DIGITS = re.compile(r"\b\d{4,30}(\d{4})\b")


def mask(text: str) -> str:
    """SBX-GM-00412277 -> SBX-GM-****2277; any run of 8+ digits -> ****1234."""
    out = _SBX.sub(lambda m: f"{m.group(1)}****{m.group(2)}", text)
    return _LONG_DIGITS.sub(lambda m: f"****{m.group(1)}", out)


def safe(text: object) -> str:
    return escape(mask(str(text)))


def p(text: object, style: object = st.BODY) -> Paragraph:
    return Paragraph(safe(text), style)  # type: ignore[arg-type]


class SectionMarker(Flowable):
    """Zero-size flowable that adds a PDF bookmark and outline entry titled with the
    section key (tests and the lender read section keys from the outline)."""

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.width = self.height = 0

    def draw(self) -> None:
        self.canv.bookmarkPage(self.key)
        self.canv.addOutlineEntry(self.key, self.key, level=0)


def _titles(fw: Framework) -> dict[str, str]:
    if fw == Framework.US_GAAP:
        return {"pnl": "Income statement", "bs": "Balance sheet"}
    return {"pnl": "Statement of profit or loss", "bs": "Statement of financial position"}


def statement_table(lines: list[StatementLine], columns: list[str], labels: list[str]) -> Table:
    rows: list[list[str]] = [["", *labels]]
    bold: list[int] = []
    for ln in lines:
        if ln.level == 0 and not ln.values_cents:
            rows.append([ln.label.upper(), *["" for _ in columns]])
            bold.append(len(rows) - 1)
            continue
        rows.append(
            [ln.label, *[fmt(ln.values_cents[c]) if c in ln.values_cents else "" for c in columns]]
        )
        if ln.level >= 2:
            bold.append(len(rows) - 1)
    safe_rows = [[mask(c) for c in r] for r in rows]
    first = 62 * mm if len(columns) > 4 else 80 * mm
    rest = (180 * mm - first) / max(1, len(columns))
    t = Table(safe_rows, colWidths=[first, *[rest] * len(columns)], repeatRows=1)
    t.setStyle(st.table_style(1, tuple(bold)))
    return t


def kv_table(rows: list[tuple[str, str]], unmasked: tuple[str, ...] = ()) -> Table:
    """Two-column table; values are masked unless their label is in ``unmasked``."""
    cells = [[mask(a), b if a in unmasked else mask(b)] for a, b in rows]
    t = Table(cells, colWidths=[80 * mm, 100 * mm])
    t.setStyle(st.table_style(0))
    return t


# --- sections ------------------------------------------------------------------------


def cover(c: LoanPackContent) -> list[Flowable]:
    return [
        p("Loan application pack", st.TITLE),
        p(c.business_name, st.H1),
        p(BANNER_TEXT, st.BANNER),
        kv_table(
            [
                ("As of", c.as_of.isoformat()),
                ("Requested amount", fmt(c.requested_amount_cents)),
                ("Term", f"{c.term_days} days"),
                ("Purpose", c.purpose),
                (
                    "Reporting framework",
                    "IFRS for SMEs" if c.framework == Framework.IFRS_SME else "US GAAP",
                ),
            ]
        ),
        Spacer(1, 4 * mm),
        p(
            "Prepared by LoanReady from the business's records. LoanReady compares and prepares; "
            "it does not lend, broker or recommend a lender.",
            st.SMALL,
        ),
    ]


def summary(c: LoanPackContent) -> list[Flowable]:
    base = c.forecast.scenarios[Scenario.BASE]
    neg = base.first_negative_week
    rows = [
        ("Cash today (bank + till)", fmt(base.weeks[0].opening_cents)),
        ("Base case: first week below zero", f"week {neg}" if neg else "none in 13 weeks"),
        ("Recommended financing gap", fmt(c.forecast.recommended_gap_cents)),
        ("Lender-readiness score", f"{c.readiness.score} / 100 ({c.readiness.band.value})"),
    ]
    out: list[Flowable] = [p("Summary", st.H1), kv_table(rows), p("Top improvements", st.H2)]
    for imp in c.readiness.improvements[:3]:
        out.append(p(f"• {imp.text}"))
    return out


def value_columns(lines: list[StatementLine], preferred: list[str] | None = None) -> list[str]:
    """Columns present in the lines: ``preferred`` order first (e.g. P&L periods), other
    keys sorted, and "total" last when any line has it."""
    keys = {k for ln in lines for k in ln.values_cents}
    cols = [k for k in (preferred or []) if k in keys]
    cols += sorted(keys - set(cols) - {"total"})
    return [*cols, "total"] if "total" in keys else cols


def _labels(cols: list[str]) -> list[str]:
    return ["Total" if c == "total" else c for c in cols]


def pnl(c: LoanPackContent) -> list[Flowable]:
    cols = value_columns(c.pnl.lines, c.pnl.periods)
    return [
        p(_titles(c.framework)["pnl"], st.H1),
        statement_table(c.pnl.lines, cols, _labels(cols)),
    ]


def cash_flow(c: LoanPackContent) -> list[Flowable]:
    cf = c.cash_flow
    cols = value_columns(cf.lines)
    return [
        p("Statement of cash flows (direct method)", st.H1),
        p(f"{cf.start.isoformat()} to {cf.end.isoformat()}", st.SMALL),
        statement_table(cf.lines, cols, _labels(cols)),
        Spacer(1, 2 * mm),
        kv_table(
            [
                ("Opening cash", fmt(cf.opening_cash_cents)),
                ("Net operating", fmt(cf.net_operating_cents)),
                ("Net investing", fmt(cf.net_investing_cents)),
                ("Net financing", fmt(cf.net_financing_cents)),
                ("Closing cash", fmt(cf.closing_cash_cents)),
            ]
        ),
    ]


def balance_sheet(c: LoanPackContent) -> list[Flowable]:
    cols = value_columns(c.balance_sheet.lines) or [c.balance_sheet.as_of.isoformat()]
    return [
        p(_titles(c.framework)["bs"], st.H1),
        p(f"As of {c.balance_sheet.as_of.isoformat()}", st.SMALL),
        statement_table(c.balance_sheet.lines, cols, _labels(cols)),
        p(
            f"Total assets {fmt(c.balance_sheet.total_assets_cents)} = liabilities "
            f"{fmt(c.balance_sheet.total_liabilities_cents)} + equity "
            f"{fmt(c.balance_sheet.total_equity_cents)}",
            st.SMALL,
        ),
    ]


def forecast(c: LoanPackContent) -> list[Flowable]:
    f = c.forecast
    low, base, high = (f.scenarios[s] for s in (Scenario.LOW, Scenario.BASE, Scenario.HIGH))
    rows = [["Week", "Start", "Low", "Base", "High"]]
    for lo, ba, hi in zip(low.weeks, base.weeks, high.weeks, strict=True):
        rows.append(
            [
                str(ba.week),
                ba.start.isoformat(),
                fmt(lo.closing_cents),
                fmt(ba.closing_cents),
                fmt(hi.closing_cents),
            ]
        )
    t = Table(rows, colWidths=[14 * mm, 28 * mm, 32 * mm, 32 * mm, 32 * mm], repeatRows=1)
    t.setStyle(st.table_style(1))
    out: list[Flowable] = [
        p("13-week cash forecast (closing cash)", st.H1),
        t,
        p(
            "Ranges, not certainty: low / base / high scenarios from the business's own history.",
            st.SMALL,
        ),
        p("Assumptions (base case)", st.H2),
    ]
    out += [p(f"• {a}", st.SMALL) for a in base.assumptions]
    if c.going_concern_note or f.going_concern_flag:
        out += [
            p("Going concern", st.H2),
            p(
                c.going_concern_note
                or "The forecast shows a material uncertainty about the business's ability to "
                "meet its obligations without new financing."
            ),
        ]
    return out


def reconciliation(c: LoanPackContent) -> list[Flowable]:
    r = c.reconciliation
    total = len(r.matched) + len(r.bank_only)
    share = pct(Decimal(len(r.matched)) / Decimal(total)) if total else "n/a"
    out: list[Flowable] = [
        p("Bank reconciliation", st.H1),
        kv_table(
            [
                ("As of", r.as_of.isoformat()),
                ("Books: cash at bank (1000)", fmt(r.ledger_cash_cents)),
                ("Bank statement balance", fmt(r.bank_balance_cents)),
                ("Difference", fmt(r.difference_cents)),
                ("Matched bank lines", f"{len(r.matched)} ({share})"),
                ("Reconciled", "yes" if r.is_reconciled else "no"),
            ]
        ),
    ]
    if r.reconciling_items:
        out.append(p("Reconciling items", st.H2))
        rows = [["Item", "Amount"]] + [
            [i.label, fmt(i.values_cents.get("amount", 0))] for i in r.reconciling_items
        ]
        t = Table([[mask(a), b] for a, b in rows], colWidths=[130 * mm, 30 * mm], repeatRows=1)
        t.setStyle(st.table_style(1))
        out.append(t)
    return out


def ageing(c: LoanPackContent) -> list[Flowable]:
    a = c.ageing
    out: list[Flowable] = [
        p("Receivables ageing", st.H1),
        kv_table([(k, fmt(v)) for k, v in a.buckets_cents.items()]),
    ]
    if a.rows:
        rows = [["Customer", "Days overdue", "Bucket", "Outstanding"]] + [
            [r.customer_name, str(r.days_overdue), r.bucket, fmt(r.outstanding_cents)]
            for r in a.rows
        ]
        t = Table([[mask(x) for x in r] for r in rows], repeatRows=1)
        t.setStyle(st.table_style(1))
        out += [Spacer(1, 2 * mm), t]
    if a.ecl_allowance_cents:
        out.append(p(f"Expected credit loss allowance: {fmt(a.ecl_allowance_cents)}"))
    return out


def sales_trend(c: LoanPackContent) -> list[Flowable]:
    weeks = c.weekly_sales[-26:]
    rows = [["Week starting", "Cash", "Card", "Total"]] + [
        [w.week_start.isoformat(), fmt(w.cash_cents), fmt(w.card_sales_cents), fmt(w.total_cents)]
        for w in weeks
    ]
    t = Table(rows, repeatRows=1)
    t.setStyle(st.table_style(1))
    return [p("Weekly point-of-sale sales", st.H1), t]


def readiness(c: LoanPackContent) -> list[Flowable]:
    r = c.readiness
    rows = [["Component", "Weight", "Points", "Metric"]] + [
        [x.label, str(x.weight), str(x.points), x.metric] for x in r.components
    ]
    t = Table([[mask(v) for v in row] for row in rows], repeatRows=1)
    t.setStyle(st.table_style(1))
    out: list[Flowable] = [
        p("Lender readiness", st.H1),
        p(f"Score {r.score} / 100, band {r.band.value}"),
        t,
        p("How to improve", st.H2),
    ]
    out += [p(f"• {i.text} (+{i.points_gain} points)") for i in r.improvements]
    out.append(p(r.disclaimer, st.SMALL))
    return out


def financing(c: LoanPackContent) -> list[Flowable]:
    out: list[Flowable] = [p("Financing options compared", st.H1)]
    fc = c.financing
    if fc is None:
        return [*out, p("No financing comparison was requested for this pack.")]
    rows = [["Option", "Provider", "Received", "Total cost", "Est. APR", "EAR", "Eligible"]]
    for o in sorted(fc.options, key=lambda o: (o.rank is None, o.rank or 0, o.product_id)):
        rows.append(
            [
                o.name,
                o.provider,
                fmt(o.amount_received_cents),
                fmt(o.total_cost_cents),
                pct(o.nominal_apr) if o.nominal_apr is not None else "n/a",
                pct(o.ear) if o.ear is not None else "n/a",
                "yes" if o.eligible else "no",
            ]
        )
    t = Table([[mask(v) for v in r] for r in rows], repeatRows=1)
    t.setStyle(st.table_style(1))
    out += [
        t,
        p(
            f"Catalogue dated {fc.catalogue_as_of.isoformat()}; source: {fc.catalogue_source}",
            st.SMALL,
        ),
    ]
    out += [p(n, st.SMALL) for n in fc.notices]
    out.append(
        p(
            "Options are ranked by annualised cost only. LoanReady does not recommend a lender "
            "and does not accept any offer on the business's behalf.",
            st.SMALL,
        )
    )
    return out


def policies(c: LoanPackContent) -> list[Flowable]:
    return [p("Accounting policies", st.H1), *[p(x) for x in c.accounting_policies]]


def controls(c: LoanPackContent) -> list[Flowable]:
    chain = {True: "intact", False: "BROKEN", None: "not checked"}[c.audit_chain_ok]
    return [
        p("Controls", st.H1),
        p(
            "Books were built by LoanReady from the business's bank feed, point-of-sale sales, "
            "invoices, bills and receipts. Personal items were separated and confirmed by the "
            "owner. Every action passed a permission gate and is recorded in a hash-chained "
            "audit log."
        ),
        kv_table(
            [
                (
                    "Bank reconciliation",
                    "reconciled" if c.reconciliation.is_reconciled else "open items",
                ),
                ("Audit log final hash", c.audit_final_hash or "n/a"),
                ("Audit chain", chain),
            ],
            unmasked=("Audit log final hash",),
        ),
    ]


SECTIONS: dict[str, Callable[[LoanPackContent], list[Flowable]]] = {
    "cover": cover,
    "summary": summary,
    "pnl": pnl,
    "cash_flow": cash_flow,
    "balance_sheet": balance_sheet,
    "forecast": forecast,
    "reconciliation": reconciliation,
    "ageing": ageing,
    "sales_trend": sales_trend,
    "readiness": readiness,
    "financing": financing,
    "policies": policies,
    "controls": controls,
}
