"""Receivables ageing (PRD/05 §9, BKS-21).

Buckets by days past due at AS_OF: current (not yet due), 1-30, 31-60, 61-90, 90+.
The ECL provision matrix (BKS-22, CTL-FR-09) is a SHOULD module and is not built yet:
``ecl_rates`` is empty and ``ecl_allowance_cents`` is 0.
"""

from __future__ import annotations

from datetime import date

from contracts.dto_books import (
    AGEING_BUCKETS,
    AgeingRow,
    CounterpartyDTO,
    InvoiceDTO,
    ReceivablesAgeingDTO,
)
from contracts.enums import InvoiceStatus


def bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def ageing(
    invoices: list[InvoiceDTO], parties: dict[str, CounterpartyDTO], as_of: date
) -> ReceivablesAgeingDTO:
    rows: list[AgeingRow] = []
    totals = dict.fromkeys(AGEING_BUCKETS, 0)
    for inv in invoices:
        if inv.status not in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL):
            continue
        if inv.outstanding_cents <= 0 or inv.issue_date > as_of:
            continue
        days = inv.days_overdue(as_of)
        b = bucket(days)
        totals[b] += inv.outstanding_cents
        name = parties[inv.customer_id].name if inv.customer_id in parties else inv.customer_id
        rows.append(
            AgeingRow(
                invoice_id=inv.id,
                customer_name=name,
                outstanding_cents=inv.outstanding_cents,
                days_overdue=days,
                bucket=b,
            )
        )
    rows.sort(key=lambda r: (-r.days_overdue, r.invoice_id))
    return ReceivablesAgeingDTO(
        as_of=as_of, buckets_cents=totals, rows=rows, ecl_rates={}, ecl_allowance_cents=0
    )
