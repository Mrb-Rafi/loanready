"""T-BKS-14, T-BKS-15, T-BKS-16 prove BKS-02..BKS-04, BKS-30: idempotent ingestion,
document matching before categorisation, cash-paid COGS only."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from contracts.enums import BillStatus, InvoiceStatus, RawStatus
from tests.books.helpers import txn


def test_ingest_idempotent(books: Any, tiny: Any) -> None:
    """T-BKS-14: re-ingesting the same bank and POS ids adds nothing."""
    books.load_bundle(tiny)
    assert len(books.ingest_bank(tiny.bank_transactions)) == 6
    assert books.ingest_bank(tiny.bank_transactions) == []
    assert len(books.ingest_pos(tiny.pos_sales)) == 3
    assert books.ingest_pos(tiny.pos_sales) == []
    books.load_bundle(tiny)
    assert len(books.repo.raw_rows()) == 9
    assert sum(e.source.value == "INVOICE" for e in books.journal()) == 1


def test_invoice_payment_matched_first(books: Any, tiny: Any) -> None:
    """T-BKS-15: an inflow equal to an open invoice from the same customer pays it."""
    books.load_bundle(tiny)
    books.ingest_bank(
        [txn("pay1", date(2026, 10, 12), 60000, "TINY CATERING PAYMENT INV-1", "Tiny Catering")]
    )
    res = books.categorise_pending(None)
    assert res[0].rule_id == "MATCH-INVOICE"
    inv = books.invoice("inv_tiny_1")
    assert inv.status == InvoiceStatus.PAID and inv.paid_cents == 60000
    assert books.open_invoices() == []
    assert books.repo.raw_rows()[0].status == RawStatus.POSTED


def test_bill_payment_matched_and_name_threshold(books: Any, tiny: Any) -> None:
    """T-BKS-15: the bill is paid only when the supplier name is similar enough."""
    books.load_bundle(tiny)
    books.ingest_bank(
        [
            txn("x", date(2026, 10, 12), -80000, "SOMEONE ELSE", "Other Ltd"),
            txn("y", date(2026, 10, 13), -80000, "TINY MILLS BILL-1", "Tiny Mills"),
        ]
    )
    books.categorise_pending(None)
    bill = books.bill("bil_tiny_1")
    assert bill.status == BillStatus.PAID
    rows = {r.source_ref: r for r in books.repo.raw_rows()}
    assert rows["y"].status == RawStatus.POSTED
    assert rows["x"].status != RawStatus.POSTED or rows["x"].label != "2000"


def test_master_data_unpaid_until_bank(books: Any, tiny: Any) -> None:
    """T-BKS-15: master data marked PAID is stored unpaid; the bank payment settles it."""
    paid = tiny.invoices[0].model_copy(update={"paid_cents": 60000, "status": InvoiceStatus.PAID})
    books.load_master_data(tiny.counterparties, tiny.payees, [paid], [])
    assert books.invoice(paid.id).status == InvoiceStatus.OPEN


def test_cogs_ratio_cash_only(tiny_books: Any) -> None:
    """T-BKS-16: the 800.00 credit bill (row 7) is not in the ratio; cash purchases are."""
    ratio = tiny_books.cogs_ratio(3, date(2026, 10, 14))
    assert ratio == Decimal("0.3000")


def test_flags_returned_not_acted_on(books: Any, tiny: Any, injection_corpus: Any) -> None:
    """T-BKS-14 / BKS-02: injected memos are flagged; no document changes, nothing paid."""
    books.load_bundle(tiny)
    lines = [
        txn(f"inj{i}", date(2026, 10, 10), 4500, e["text"][:200])
        for i, e in enumerate(injection_corpus)
    ]
    books.ingest_bank(lines)
    flagged = {f.source_ref for f in books.last_scan_flags()}
    expected = {f"inj{i}" for i, e in enumerate(injection_corpus) if e["expect_flag"]}
    assert flagged & expected
    books.categorise_pending(None)
    books.post_ready()
    assert books.bill("bil_tiny_1").status == BillStatus.OPEN
    assert books.invoice("inv_tiny_1").status == InvoiceStatus.OPEN


def test_rina_documents_settled(rina_books: Any, rina_bundle: Any) -> None:
    """T-BKS-15 on the bakery: every bundle-PAID document is paid by its bank line; the
    open ones are the scenario documents."""
    paid_in_bundle = {i.id for i in rina_bundle.invoices if i.status == InvoiceStatus.PAID}
    assert paid_in_bundle == {
        i.id for i in rina_books.repo.invoices() if i.status == InvoiceStatus.PAID
    }
    open_bills = sorted(b.number for b in rina_books.open_bills())
    assert open_bills == ["BILL-2190", "BILL-2207", "BILL-2211", "BILL-2215"]
