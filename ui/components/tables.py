"""Display tables built from DTOs (PRD/12 §1).

Display only: every money cell is ``contracts.money.fmt`` of a DTO int cents value and every
rate is ``contracts.money.pct`` of a DTO Decimal. No sums, no conversions, no floats. Each
builder returns a ``pandas.DataFrame`` of strings with plain column headers (UI-13).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

import pandas as pd

from contracts.actions import AuditRecord, Proposal, mask_contact
from contracts.canonical import canonical_json
from contracts.dto_app import ControlRow, TierRow
from contracts.dto_books import (
    AgeingRow,
    JournalEntryDTO,
    ReconMatch,
    StatementLine,
)
from contracts.dto_financing import FinancingOption, ReadinessComponent
from contracts.dto_planning import WeekRow
from ui.components.money import NA, money, rate, signed_money

_INDENT = {0: "", 1: "    ", 2: "  ", 3: ""}


def statement_df(lines: Sequence[StatementLine], periods: Sequence[str]) -> pd.DataFrame:
    """One row per statement line, one column per period; headings and totals marked."""
    rows = []
    for line in lines:
        label = _INDENT.get(line.level, "") + line.label
        if line.level == 0:
            label = label.upper()
        elif line.level == 3:
            label = f"Total: {line.label}" if not line.label.lower().startswith("total") else label
        row = {"Line": label}
        for p in periods:
            v = line.values_cents.get(p)
            row[p] = "" if v is None else money(v)
        rows.append(row)
    return pd.DataFrame(rows, columns=["Line", *periods])


def statement_periods(lines: Iterable[StatementLine]) -> list[str]:
    """Period keys in first-seen order (for statements without an explicit ``periods`` list)."""
    seen: dict[str, None] = {}
    for line in lines:
        for k in line.values_cents:
            seen.setdefault(k, None)
    return list(seen)


def journal_df(entries: Sequence[JournalEntryDTO]) -> pd.DataFrame:
    rows = []
    for e in entries:
        for ln in e.lines:
            rows.append(
                {
                    "Date": e.date.isoformat(),
                    "Entry": e.id,
                    "Memo": str(e.memo),
                    "Source": e.source.value,
                    "Status": e.status.value,
                    "Account": ln.account_code,
                    "Debit": money(ln.debit_cents) if ln.debit_cents else "",
                    "Credit": money(ln.credit_cents) if ln.credit_cents else "",
                }
            )
    cols = ["Date", "Entry", "Memo", "Source", "Status", "Account", "Debit", "Credit"]
    return pd.DataFrame(rows, columns=cols)


def weeks_df(weeks: Sequence[WeekRow]) -> pd.DataFrame:
    rows = [
        {
            "Week": w.week,
            "From": w.start.isoformat(),
            "To": w.end.isoformat(),
            "Opening": money(w.opening_cents),
            "Sales in": money(w.in_sales_cents),
            "Collections in": money(w.in_collections_cents),
            "COGS out": money(w.out_cogs_cents),
            "Bills out": money(w.out_bills_cents),
            "Rent out": money(w.out_rent_cents),
            "Wages out": money(w.out_wages_cents),
            "Utilities out": money(w.out_utilities_cents),
            "Loan out": money(w.out_loan_cents),
            "Other out": money(w.out_other_cents),
            "Net": signed_money(w.net_cents),
            "Closing": money(w.closing_cents),
            "Below zero": "yes" if w.closing_cents < 0 else "no",
        }
        for w in weeks
    ]
    return pd.DataFrame(rows)


def band_df(
    low: Sequence[WeekRow], base: Sequence[WeekRow], high: Sequence[WeekRow]
) -> pd.DataFrame:
    """Text alternative to the forecast chart (UI-13): closing cash per week per scenario."""
    rows = []
    for lo, ba, hi in zip(low, base, high, strict=False):
        rows.append(
            {
                "Week": ba.week,
                "From": ba.start.isoformat(),
                "Low closing": money(lo.closing_cents),
                "Base closing": money(ba.closing_cents),
                "High closing": money(hi.closing_cents),
            }
        )
    return pd.DataFrame(rows)


def _ear_key(o: FinancingOption) -> tuple[int, Decimal]:
    if not o.eligible:
        return (2, Decimal(0))
    if o.ear is None:
        return (1, Decimal(0))
    return (0, o.ear)


def sort_by_ear(options: Sequence[FinancingOption]) -> list[FinancingOption]:
    """Eligible options by EAR ascending, then options without an EAR, then ineligible."""
    return sorted(options, key=_ear_key)


def options_df(
    options: Sequence[FinancingOption], cheapest_id: str | None, dearest_id: str | None
) -> pd.DataFrame:
    rows = []
    for o in sort_by_ear(options):
        mark = ""
        if o.product_id == cheapest_id:
            mark = "Cheapest"
        elif o.product_id == dearest_id:
            mark = "Most expensive"
        d = o.disclosure
        rows.append(
            {
                "Cost rank": mark,
                "Option": o.name,
                "Provider": o.provider,
                "You receive": money(o.amount_received_cents),
                "Total cost": money(o.total_cost_cents),
                "Estimated APR": rate(o.nominal_apr),
                "EAR": rate(o.ear),
                "Payments": f"{d.number_of_payments} x {money(d.payment_amount_cents)} "
                f"({d.payment_frequency})",
                "Eligible": "yes" if o.eligible else "no: " + "; ".join(o.ineligibility_reasons),
                "Flags": ", ".join(o.flags),
            }
        )
    return pd.DataFrame(rows)


def readiness_df(components: Sequence[ReadinessComponent]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Component": c.label,
                "Points": f"{c.points} / {c.weight}",
                "Metric": c.metric,
                "Rule": c.rule,
            }
            for c in components
        ]
    )


def ageing_df(rows: Sequence[AgeingRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Invoice": r.invoice_id,
                "Customer": r.customer_name,
                "Outstanding": money(r.outstanding_cents),
                "Days overdue": r.days_overdue,
                "Bucket": r.bucket,
            }
            for r in rows
        ],
        columns=["Invoice", "Customer", "Outstanding", "Days overdue", "Bucket"],
    )


def buckets_df(
    buckets_cents: dict[str, int], ecl_rates: dict[str, Decimal], order: Sequence[str]
) -> pd.DataFrame:
    keys = [k for k in order if k in buckets_cents] + [k for k in buckets_cents if k not in order]
    return pd.DataFrame(
        [
            {
                "Bucket": k,
                "Outstanding": money(buckets_cents[k]),
                "ECL rate": rate(ecl_rates.get(k)) if ecl_rates else NA,
            }
            for k in keys
        ]
    )


def matches_df(matches: Sequence[ReconMatch]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Bank txn": m.bank_txn_id,
                "Journal entry": m.journal_entry_id,
                "Amount": signed_money(m.amount_cents),
                "Date difference (days)": m.date_diff_days,
                "Score": str(m.score),
            }
            for m in matches
        ],
        columns=["Bank txn", "Journal entry", "Amount", "Date difference (days)", "Score"],
    )


def _short(value: object) -> str:
    if value is None:
        return ""
    return canonical_json(value) if isinstance(value, (dict, list)) else str(value)


def audit_df(rows: Sequence[AuditRecord]) -> pd.DataFrame:
    """Newest first."""
    cols = [
        "Seq",
        "Time (UTC)",
        "Action",
        "Tier",
        "Decision",
        "Reason",
        "Principal",
        "Approved by",
        "Sandbox ref",
        "Controls",
        "Params (masked)",
        "Row hash",
    ]
    out = []
    for r in sorted(rows, key=lambda r: r.seq, reverse=True):
        out.append(
            {
                "Seq": r.seq,
                "Time (UTC)": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "Action": r.action_type,
                "Tier": "" if r.tier is None else str(r.tier),
                "Decision": r.decision.value,
                "Reason": r.reason_code.value,
                "Principal": r.principal_id,
                "Approved by": r.approved_by or "",
                "Sandbox ref": r.sbx_ref or "",
                "Controls": ", ".join(r.control_ids),
                "Params (masked)": _short(r.params_masked),
                "Row hash": r.row_hash[:12] + "…",
            }
        )
    return pd.DataFrame(out, columns=cols)


def counts_df(counts: dict[str, int]) -> pd.DataFrame:
    return pd.DataFrame([{"Decision": k, "Rows": v} for k, v in counts.items()])


def tiers_df(rows: Sequence[TierRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Action type": r.action_type,
                "Tier": r.tier,
                "Description": r.description,
                "Safeguards": "; ".join(r.safeguards),
            }
            for r in rows
        ]
    )


def controls_df(rows: Sequence[ControlRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Control": r.control_id,
                "Title": r.title,
                "Designed around": ", ".join(r.regulation_refs),
                "Implemented in": r.implemented_in,
                "Tests": ", ".join(r.tests),
            }
            for r in rows
        ]
    )


def display_party(name: str | None) -> str:
    """Payee/recipient for display: contacts (email, phone) are masked, names shown as is."""
    if not name:
        return ""
    if "@" in name or name.startswith("+"):
        return mask_contact(name)
    return name


def proposals_df(proposals: Sequence[Proposal]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Proposal": p.proposal_id,
                "Action": p.action.type.value,
                "Amount": money(p.summary.amount_cents, p.summary.currency),
                "To": display_party(p.summary.counterparty_name),
                "Reason": p.summary.reason,
                "Status": p.status.value,
            }
            for p in proposals
        ]
    )
