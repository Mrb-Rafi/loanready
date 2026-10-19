"""T-BKS-08 proves BKS-20, CTL-FR-04, REG-IC-07: matched / bank-only / books-only, and
the difference equals the sum of the itemised reconciling items."""

from __future__ import annotations

from datetime import date
from typing import Any

from tests.books.helpers import txn

AS_OF = date(2026, 10, 14)


def test_tiny_reconciles_exactly(tiny_books: Any, tiny: Any) -> None:
    """T-BKS-08: every tiny bank line matches its entry; difference 0."""
    rec = tiny_books.reconcile(tiny.bank_transactions, 271000, AS_OF)
    assert len(rec.matched) == len(tiny.bank_transactions)
    assert rec.bank_only == [] and rec.books_only == []
    assert rec.ledger_cash_cents == rec.bank_balance_cents == 271000
    assert rec.difference_cents == 0 and rec.is_reconciled


def test_bank_only_and_books_only(tiny_books: Any, tiny: Any, owner: Any) -> None:
    """T-BKS-08: an unconfirmed bank line is bank-only; a manual payment is books-only."""
    extra = txn("t_unknown", date(2026, 10, 12), -4500, "CORNER HARDWARE")
    tiny_books.ingest_bank([extra])
    tiny_books.categorise_pending(None)
    tiny_books.post_ready()
    tiny_books.record_bill_payment("bil_tiny_1", 20000, date(2026, 10, 13), "cheque-17")
    bank = [*tiny.bank_transactions, extra]
    rec = tiny_books.reconcile(bank, 271000 - 4500, AS_OF)
    assert rec.bank_only == ["t_unknown"]
    assert len(rec.books_only) == 1
    items = {i.key: i.values_cents["amount"] for i in rec.reconciling_items}
    assert items["t_unknown"] == 4500
    assert items[rec.books_only[0]] == -20000
    assert rec.difference_cents == 4500 - 20000
    assert rec.is_reconciled


def test_date_window(tiny_books: Any, tiny: Any) -> None:
    """T-BKS-08: a bank line 4 days from its entry does not match (window +/- 3 days)."""
    moved = [
        t.model_copy(update={"date": date(2026, 10, 13)}) if t.id == "txn_tiny_06" else t
        for t in tiny.bank_transactions
    ]
    rec = tiny_books.reconcile(moved, 271000, AS_OF)
    assert "txn_tiny_06" in rec.bank_only and rec.books_only
    assert rec.is_reconciled


def test_rina_reconciled_with_items(rina_books: Any, rina_bundle: Any) -> None:
    """T-BKS-08 / identity 5 on the seeded bakery: ledger - bank = sum(reconciling items)."""
    rec = rina_books.reconcile(
        rina_bundle.bank_transactions, rina_bundle.bank_accounts[0].balance_cents, rina_bundle.as_of
    )
    assert rec.is_reconciled
    assert rec.books_only == []
    assert len(rec.duplicates) == 1
