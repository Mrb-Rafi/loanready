"""Ingestion (PRD/05 §2): bank feed and POS sales -> raw rows; document matching.

Descriptions and notes are untrusted: they are stored and scanned (visibility only) and
never interpreted (BKS-02, BKS-03, CON-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from books.repo import RawRow
from books.similarity import jaccard, name_in
from contracts.canonical import params_hash
from contracts.dto_books import BillDTO, CounterpartyDTO, InvoiceDTO
from contracts.enums import BillStatus, InvoiceStatus, RawStatus, SourceKind
from contracts.ids import IdFactory
from contracts.sandbox_api import BankTxnOut, PosSaleOut
from contracts.untrusted import ScanFlag, Untrusted, scan_untrusted

NAME_MATCH = Decimal("0.8")


def bank_raw(t: BankTxnOut, ids: IdFactory) -> RawRow:
    return RawRow(
        id=ids.new("rtx"),
        source=SourceKind.BANK,
        source_ref=t.id,
        date=t.date,
        amount_cents=t.amount_cents,
        description=str(t.description),
        counterparty_hint=t.counterparty,
        tender=None,
        payload_hash=params_hash(t),
        status=RawStatus.NEW,
        label=None,
        method=None,
        confidence=None,
        journal_entry_id=None,
    )


def pos_raw(s: PosSaleOut, ids: IdFactory) -> RawRow:
    return RawRow(
        id=ids.new("rtx"),
        source=SourceKind.POS,
        source_ref=s.id,
        date=s.ts.date(),
        amount_cents=s.amount_cents,
        description=str(s.note) if s.note else "POS sale",
        counterparty_hint=None,
        tender=s.tender,
        payload_hash=params_hash(s),
        status=RawStatus.NEW,
        label=None,
        method=None,
        confidence=None,
        journal_entry_id=None,
    )


def scan(text: str, source: SourceKind, ref: str) -> list[ScanFlag]:
    return scan_untrusted(Untrusted(text, source, ref))


@dataclass(frozen=True)
class Match:
    kind: str  # "invoice" | "bill" | "card_settlement" | "cash_deposit"
    doc_id: str | None = None
    number: str | None = None


def _party_score(name: str, row: RawRow) -> Decimal:
    """Counterparty similarity: Jaccard on the bank counterparty field, or the share of
    the name found in the memo, whichever is higher."""
    return max(jaccard(name, row.counterparty_hint), name_in(name, row.description))


def match_document(
    row: RawRow,
    invoices: list[InvoiceDTO],
    bills: list[BillDTO],
    parties: dict[str, CounterpartyDTO],
    as_of: date,
) -> Match | None:
    """BKS-04, in order: invoice payment (inflow = an open invoice's outstanding, name
    similarity >= 0.8, date in [issue_date, as_of]); bill payment (outflow = an open bill's
    outstanding, supplier similarity >= 0.8); CARD SETTLEMENT; CASH DEPOSIT. Ties prefer the
    document whose number appears in the memo, then the oldest."""
    text = row.description.upper()
    if row.amount_cents > 0:
        cands = [
            i
            for i in invoices
            if i.status in (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)
            and i.outstanding_cents == row.amount_cents
            and i.issue_date <= row.date <= as_of
            and _party_score(parties[i.customer_id].name, row) >= NAME_MATCH
        ]
        if cands:
            best = min(cands, key=lambda i: (i.number not in text, i.issue_date, i.number))
            return Match("invoice", best.id, best.number)
    elif row.amount_cents < 0:
        cands_b = [
            b
            for b in bills
            if b.status in (BillStatus.OPEN, BillStatus.PARTIAL)
            and b.outstanding_cents == -row.amount_cents
            and b.issue_date <= row.date
            and _party_score(parties[b.supplier_id].name, row) >= NAME_MATCH
        ]
        if cands_b:
            best_b = min(cands_b, key=lambda b: (b.number not in text, b.issue_date, b.number))
            return Match("bill", best_b.id, best_b.number)
    if text.startswith("CARD SETTLEMENT") and row.amount_cents > 0:
        return Match("card_settlement")
    if text.startswith("CASH DEPOSIT") and row.amount_cents > 0:
        return Match("cash_deposit")
    return None
