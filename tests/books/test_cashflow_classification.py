"""T-BKS-09 proves BKS-17, CTL-FR-08, REG-ACC-06: interest is financing under IFRS for
SMEs and operating under US GAAP; drawings are financing; 1000 <-> 1010 is excluded."""

from __future__ import annotations

from datetime import date

from books import posting, statements
from contracts.dto_books import JournalEntryDTO
from contracts.enums import EntryStatus, Framework

D = date(2026, 10, 5)


def _entry(i: int, draft: posting.EntryDraft) -> JournalEntryDTO:
    return JournalEntryDTO(
        id=f"je_{i}",
        date=draft.date,
        memo=draft.memo,
        source=draft.source,
        source_ref=draft.source_ref,
        status=EntryStatus.POSTED,
        lines=list(draft.lines),
        created_by="t",
        reversal_of=None,
    )


ENTRIES = [
    _entry(0, posting.bank_categorised(date(2026, 9, 1), "o", 100000, "3000", "capital")),
    _entry(1, posting.bank_categorised(D, "a", -1500, "7000", "LOAN INTEREST")),
    _entry(2, posting.bank_categorised(D, "b", -2400, "PERSONAL", "CINEPLEX")),
    _entry(3, posting.cash_deposit(D, "c", 5000)),
    _entry(4, posting.bank_categorised(D, "d", -60000, "1500", "OVEN")),
    _entry(5, posting.bank_categorised(D, "e", 50000, "2500", "LOAN RECEIVED")),
]


def _line(cf: object, key: str) -> int | None:
    return next((ln.values_cents["total"] for ln in cf.lines if ln.key == key), None)  # type: ignore[attr-defined]


def test_interest_ifrs_financing() -> None:
    """T-BKS-09: IFRS for SMEs -> interest paid under financing."""
    cf = statements.cash_flow_statement(ENTRIES, D, D, Framework.IFRS_SME)
    assert _line(cf, "interest_paid") == -1500 and _line(cf, "interest_paid_op") is None
    assert cf.net_operating_cents == 0
    assert cf.net_financing_cents == -1500 - 2400 + 50000
    assert cf.net_investing_cents == -60000


def test_interest_gaap_operating() -> None:
    """T-BKS-09: US GAAP -> interest paid under operating."""
    cf = statements.cash_flow_statement(ENTRIES, D, D, Framework.US_GAAP)
    assert _line(cf, "interest_paid_op") == -1500 and _line(cf, "interest_paid") is None
    assert cf.net_operating_cents == -1500
    assert cf.net_financing_cents == -2400 + 50000


def test_drawings_financing_and_transfer_excluded() -> None:
    """T-BKS-09: drawings in financing; the cash deposit moves nothing; closing = ledger."""
    cf = statements.cash_flow_statement(ENTRIES, D, D, Framework.IFRS_SME)
    assert _line(cf, "drawings") == -2400
    assert cf.opening_cash_cents == 100000
    assert cf.closing_cash_cents == statements.cash_balance(ENTRIES, D)


def test_labels_differ_by_framework() -> None:
    """T-BKS-09 / CTL-FR-08: framework-specific titles and policy text."""
    from books.policies import accounting_policies, labels, policies

    assert policies(Framework.IFRS_SME) == accounting_policies(Framework.IFRS_SME)

    assert labels(Framework.IFRS_SME)["balance_sheet"] == "Statement of financial position"
    assert labels(Framework.US_GAAP)["balance_sheet"] == "Balance sheet"
    ifrs = " ".join(accounting_policies(Framework.IFRS_SME))
    gaap = " ".join(accounting_policies(Framework.US_GAAP))
    assert "IFRS for SMEs" in ifrs and "ASC 606" in gaap
    for text in (ifrs, gaap):
        assert "compliant" not in text.lower() and "certified" not in text.lower()
        assert "1000" in text and "1010" in text
