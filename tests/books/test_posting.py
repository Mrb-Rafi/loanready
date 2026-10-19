"""T-BKS-01 proves CTL-FR-01, BKS-11: every posting rule balances; unbalanced and zero
entries are rejected; a reversal mirrors the original."""

from __future__ import annotations

from datetime import date

import pytest

from books import posting
from contracts.dto_books import JournalEntryDTO, JournalLineDTO
from contracts.enums import EntrySource, EntryStatus
from contracts.errors import UnbalancedEntry

D = date(2026, 10, 1)


def _balanced(d: posting.EntryDraft) -> bool:
    return sum(ln.debit_cents for ln in d.lines) == sum(ln.credit_cents for ln in d.lines) > 0


@pytest.mark.parametrize(
    "draft",
    [
        posting.pos_sale(D, "p1", 1250, "cash"),
        posting.pos_sale(D, "p2", 1250, "card"),
        posting.card_settlement(D, "t1", 35460, "CARD SETTLEMENT 2026-09-30 GROSS 360.00 FEE 5.40"),
        posting.card_settlement(D, "t2", 35460, "CARD SETTLEMENT no payload"),
        posting.cash_deposit(D, "t3", 90000),
        posting.invoice_issued(D, "i1", 60000, "INV-1"),
        posting.customer_payment(D, "t4", 60000, "INV-1"),
        posting.bill_received(D, "b1", 80000, "BILL-1", "5000"),
        posting.bill_payment(D, "t5", 80000, "BILL-1"),
        posting.bank_categorised(D, "t6", -30000, "5000", "GRAIN DEPOT"),
        posting.bank_categorised(D, "t7", -8640, "PERSONAL", "FRESHMART"),
        posting.bank_categorised(D, "t8", 4500, "4000", "deposit"),
        posting.bank_categorised(D, "t9", -650000, "1500", "OVEN"),
        posting.refund_claim(D, "t10", 12000, "refund"),
        posting.till_payment(D, "r1", 6420, "5000", "receipt"),
        posting.opening(
            D,
            [
                JournalLineDTO(account_code="1000", debit_cents=100, credit_cents=0),
                JournalLineDTO(account_code="1500", debit_cents=900, credit_cents=0),
                JournalLineDTO(account_code="3000", debit_cents=0, credit_cents=1000),
            ],
        ),
    ],
)
def test_every_row_balances(draft: posting.EntryDraft) -> None:
    """T-BKS-01: rows 1-14 balance; each line has exactly one positive side."""
    assert _balanced(draft)
    assert all((ln.debit_cents > 0) != (ln.credit_cents > 0) for ln in draft.lines)


def test_card_settlement_books_fee() -> None:
    """T-BKS-01 row 3: Dr 1000 net + Dr 6400 fee, Cr 1020 gross."""
    d = posting.card_settlement(D, "t", 35460, "CARD SETTLEMENT 2026-09-30 GROSS 360.00 FEE 5.40")
    lines = {(ln.account_code, ln.debit_cents, ln.credit_cents) for ln in d.lines}
    assert lines == {("1000", 35460, 0), ("6400", 540, 0), ("1020", 0, 36000)}


def test_personal_posts_to_drawings() -> None:
    """T-BKS-01 row 10 / BKS-08: PERSONAL is Dr 3100, never an expense."""
    d = posting.bank_categorised(D, "t", -2400, "PERSONAL", "CINEPLEX")
    assert {ln.account_code for ln in d.lines} == {"3100", "1000"}


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [JournalLineDTO(account_code="1000", debit_cents=100, credit_cents=0)],
        [
            JournalLineDTO(account_code="1000", debit_cents=100, credit_cents=0),
            JournalLineDTO(account_code="4000", debit_cents=0, credit_cents=99),
        ],
        [
            JournalLineDTO(account_code="1000", debit_cents=0, credit_cents=0),
            JournalLineDTO(account_code="4000", debit_cents=0, credit_cents=0),
        ],
        [
            JournalLineDTO(account_code="1000", debit_cents=100, credit_cents=100),
            JournalLineDTO(account_code="4000", debit_cents=0, credit_cents=0),
        ],
        [
            JournalLineDTO(account_code="9999", debit_cents=100, credit_cents=0),
            JournalLineDTO(account_code="4000", debit_cents=0, credit_cents=100),
        ],
    ],
)
def test_rejects_bad_entries(lines: list[JournalLineDTO]) -> None:
    """T-BKS-01: unbalanced, zero, two-sided and unknown-account entries are rejected."""
    with pytest.raises(UnbalancedEntry):
        posting.validate(lines)


def test_zero_amount_rule_rejected() -> None:
    """T-BKS-01: a zero-amount POS sale cannot be posted."""
    with pytest.raises(UnbalancedEntry):
        posting.pos_sale(D, "p", 0, "cash")


def test_reversal_mirrors() -> None:
    """T-BKS-01 row 16: debits become credits; reversal_of points at the original."""
    original = JournalEntryDTO(
        id="je_x",
        date=D,
        memo="x",
        source=EntrySource.BANK,
        source_ref="t",
        status=EntryStatus.POSTED,
        lines=list(posting.bank_categorised(D, "t", -500, "5000", "x").lines),
        created_by="system",
        reversal_of=None,
    )
    rev = posting.reversal(original, D, "fix")
    assert rev.reversal_of == "je_x" and rev.source == EntrySource.REVERSAL
    for a, b in zip(original.lines, rev.lines, strict=True):
        assert (a.debit_cents, a.credit_cents) == (b.credit_cents, b.debit_cents)


def test_reverse_entry_in_books(tiny_books: object, owner: object) -> None:
    """T-BKS-01 / CTL-FR-02: reversing keeps the original (status REVERSED) and nets to 0."""
    books = tiny_books
    entry = next(e for e in books.journal() if e.source == EntrySource.BANK)
    before = books.cash_balance(date(2026, 10, 14))
    new_id = books.reverse_entry(entry.id, owner, "correction")
    journal = {e.id: e for e in books.journal()}
    assert journal[entry.id].status == EntryStatus.REVERSED
    assert journal[new_id].reversal_of == entry.id
    delta = sum(
        ln.debit_cents - ln.credit_cents
        for ln in journal[new_id].lines
        if ln.account_code in ("1000", "1010")
    )
    assert books.cash_balance(date(2026, 10, 14)) == before + delta
