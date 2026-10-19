"""Bank reconciliation (PRD/05 §8, BKS-20, CTL-FR-04, REG-IC-07).

Each bank line is matched one-to-one to a journal entry that hits 1000 with the same signed
amount and a date within +/- 3 days, preferring the same ``source_ref`` (the bank txn id),
then the closest date, then counterparty similarity; greedy by score with a deterministic
tie-break by id.

difference = ledger 1000 - bank balance. Reconciling items: +amount for each books-only
entry, -amount for each bank-only line. ``is_reconciled = difference == sum(items)``.
OPENING entries (and bank lines of kind "opening") are the agreed starting balance on both
sides, so they are not matched and not listed.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from books.similarity import jaccard
from contracts.dto_books import JournalEntryDTO, ReconciliationDTO, ReconMatch, StatementLine
from contracts.enums import EntrySource
from contracts.money import fmt
from contracts.sandbox_api import BankTxnOut

MAX_DAYS = 3
BANK = "1000"


def _bank_amount(e: JournalEntryDTO) -> int:
    return sum(ln.debit_cents - ln.credit_cents for ln in e.lines if ln.account_code == BANK)


def reconcile(
    entries: list[JournalEntryDTO],
    bank_txns: list[BankTxnOut],
    bank_balance_cents: int,
    as_of: date,
    duplicates: list[list[str]],
) -> ReconciliationDTO:
    books = [
        e
        for e in entries
        if e.date <= as_of and e.source != EntrySource.OPENING and _bank_amount(e) != 0
    ]
    bank = [t for t in bank_txns if t.date <= as_of and t.kind != "opening"]
    pairs: list[tuple[Decimal, str, str, BankTxnOut, JournalEntryDTO]] = []
    for t in bank:
        for e in books:
            if _bank_amount(e) != t.amount_cents:
                continue
            diff = abs((t.date - e.date).days)
            if diff > MAX_DAYS:
                continue
            score = (
                (Decimal(2) if e.source_ref == t.id else Decimal(0))
                + Decimal(1) / Decimal(1 + diff)
                + jaccard(t.counterparty or t.description, e.memo) / Decimal(10)
            )
            pairs.append((score, t.id, e.id, t, e))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
    used_t: set[str] = set()
    used_e: set[str] = set()
    matched: list[ReconMatch] = []
    for score, tid, eid, t, e in pairs:
        if tid in used_t or eid in used_e:
            continue
        used_t.add(tid)
        used_e.add(eid)
        matched.append(
            ReconMatch(
                bank_txn_id=tid,
                journal_entry_id=eid,
                amount_cents=t.amount_cents,
                date_diff_days=abs((t.date - e.date).days),
                score=score.quantize(Decimal("0.0001")),
            )
        )
    matched.sort(key=lambda m: m.bank_txn_id)
    bank_only = [t for t in bank if t.id not in used_t]
    books_only = [e for e in books if e.id not in used_e]
    items = [
        StatementLine(
            key=t.id,
            label=f"In bank, not in books: {t.date} {fmt(t.amount_cents)}",
            values_cents={"amount": -t.amount_cents},
            level=1,
        )
        for t in bank_only
    ] + [
        StatementLine(
            key=e.id,
            label=f"In books, not in bank: {e.date} {e.memo[:40]}",
            values_cents={"amount": _bank_amount(e)},
            level=1,
        )
        for e in books_only
    ]
    ledger = sum(
        ln.debit_cents - ln.credit_cents
        for e in entries
        if e.date <= as_of or e.source == EntrySource.OPENING
        for ln in e.lines
        if ln.account_code == BANK
    )
    difference = ledger - bank_balance_cents
    return ReconciliationDTO(
        as_of=as_of,
        matched=matched,
        bank_only=[t.id for t in bank_only],
        books_only=[e.id for e in books_only],
        duplicates=duplicates,
        ledger_cash_cents=ledger,
        bank_balance_cents=bank_balance_cents,
        difference_cents=difference,
        reconciling_items=items,
        is_reconciled=difference == sum(i.values_cents["amount"] for i in items),
    )
