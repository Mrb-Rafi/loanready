"""T-BKS-10 proves BKS-21: ageing buckets (the ECL provision matrix is SHOULD, not built)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from books.receivables import ageing, bucket
from contracts.testing.builders import make_counterparty, make_invoice

AS_OF = date(2026, 10, 14)


def test_bucket_edges() -> None:
    """T-BKS-10: 0 current, 1 and 30 -> 1-30, 31 -> 31-60, 75 -> 61-90, 91 -> 90+."""
    assert [bucket(d) for d in (0, 1, 30, 31, 60, 61, 75, 90, 91)] == [
        "current",
        "1-30",
        "1-30",
        "31-60",
        "31-60",
        "61-90",
        "61-90",
        "61-90",
        "90+",
    ]


def test_ageing_rows_and_totals() -> None:
    """T-BKS-10: an S9-style invoice 75 days overdue lands in 61-90."""
    cust = make_counterparty()
    invs = [
        make_invoice(
            id="inv_a", customer_id=cust.id, due_date=AS_OF - timedelta(days=75), amount_cents=60000
        ),
        make_invoice(
            id="inv_b", customer_id=cust.id, due_date=AS_OF + timedelta(days=3), amount_cents=20000
        ),
    ]
    dto = ageing(invs, {cust.id: cust}, AS_OF)
    assert dto.buckets_cents["61-90"] == 60000 and dto.buckets_cents["current"] == 20000
    assert [r.invoice_id for r in dto.rows] == ["inv_a", "inv_b"]
    assert dto.rows[0].days_overdue == 75


def test_tiny_ageing(tiny_books: Any) -> None:
    """T-BKS-10: tiny INV-1 600.00 due 10-09 is 5 days overdue at AS_OF."""
    dto = tiny_books.receivables_ageing(AS_OF)
    assert dto.buckets_cents["1-30"] == 60000
    assert dto.rows[0].days_overdue == 5
