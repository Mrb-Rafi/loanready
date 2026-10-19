"""Posting rules (PRD/05 §4) -> balanced journal drafts.

Every entry must balance: sum(debits) == sum(credits) > 0, each line has exactly one of
debit/credit > 0 (CTL-FR-01, REG-ACC-03). Amounts in lines are positive int cents; the sign
lives in the debit/credit side. Posted entries are never edited: corrections are a
REVERSAL entry (row 16) that mirrors the original (CTL-FR-02, REG-ACC-08).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from contracts.dto_books import JournalEntryDTO, JournalLineDTO
from contracts.enums import EntrySource
from contracts.errors import UnbalancedEntry
from contracts.money import parse
from contracts.registry import CHART_OF_ACCOUNTS, PERSONAL_LABEL

ACCOUNT_CODES = frozenset(a.code for a in CHART_OF_ACCOUNTS)
BANK = "1000"
TILL = "1010"
CARD_CLEARING = "1020"
AR = "1100"
REFUND_CLAIMS = "1110"
AP = "2000"
CAPITAL = "3000"
DRAWINGS = "3100"
SALES = "4000"
FEES = "6400"
OTHER_EXPENSES = "6900"


@dataclass(frozen=True)
class EntryDraft:
    """An entry before it gets an id: date, memo, source, source_ref, lines."""

    date: date
    memo: str
    source: EntrySource
    source_ref: str
    lines: tuple[JournalLineDTO, ...]
    reversal_of: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


def dr(code: str, cents: int) -> JournalLineDTO:
    return JournalLineDTO(account_code=code, debit_cents=cents, credit_cents=0)


def cr(code: str, cents: int) -> JournalLineDTO:
    return JournalLineDTO(account_code=code, debit_cents=0, credit_cents=cents)


def validate(lines: tuple[JournalLineDTO, ...] | list[JournalLineDTO]) -> None:
    """Raise UnbalancedEntry unless sum(debits) == sum(credits) > 0, every line has exactly
    one positive side, and every account exists in the chart of accounts."""
    if not lines:
        raise UnbalancedEntry("entry has no lines")
    for ln in lines:
        if ln.account_code not in ACCOUNT_CODES:
            raise UnbalancedEntry(f"unknown account {ln.account_code}")
        if ln.debit_cents < 0 or ln.credit_cents < 0:
            raise UnbalancedEntry("negative amount in a line")
        if (ln.debit_cents > 0) == (ln.credit_cents > 0):
            raise UnbalancedEntry("each line needs exactly one of debit or credit > 0")
    debits = sum(ln.debit_cents for ln in lines)
    credits = sum(ln.credit_cents for ln in lines)
    if debits != credits or debits <= 0:
        raise UnbalancedEntry(f"debits {debits} != credits {credits}")


def _draft(d: date, memo: str, source: EntrySource, ref: str, *lines: JournalLineDTO) -> EntryDraft:
    validate(lines)
    return EntryDraft(d, memo, source, ref, tuple(lines))


def pos_sale(d: date, ref: str, cents: int, tender: str) -> EntryDraft:
    """Rows 1-2: cash sale Dr 1010 / card sale Dr 1020, Cr 4000 Sales."""
    debit = TILL if tender == "cash" else CARD_CLEARING
    return _draft(d, f"POS {tender} sale", EntrySource.POS, ref, dr(debit, cents), cr(SALES, cents))


_SETTLEMENT_RE = re.compile(r"GROSS\s+([0-9][0-9,]*\.[0-9]{2})\s+FEE\s+([0-9][0-9,]*\.[0-9]{2})")


def card_settlement(d: date, ref: str, net_cents: int, description: str) -> EntryDraft:
    """Row 3: Dr 1000 net + Dr 6400 fee, Cr 1020 gross. Gross and fee are read from the
    bank description ("GROSS 360.00 FEE 5.40"); if absent or inconsistent
    (gross != net + fee), the whole net amount clears 1020 and no fee is booked."""
    m = _SETTLEMENT_RE.search(description.upper())
    if m:
        gross, fee = parse(m.group(1)), parse(m.group(2))
        if gross == net_cents + fee and fee > 0:
            return _draft(
                d,
                "Card settlement",
                EntrySource.BANK,
                ref,
                dr(BANK, net_cents),
                dr(FEES, fee),
                cr(CARD_CLEARING, gross),
            )
    return _draft(
        d,
        "Card settlement",
        EntrySource.BANK,
        ref,
        dr(BANK, net_cents),
        cr(CARD_CLEARING, net_cents),
    )


def cash_deposit(d: date, ref: str, cents: int) -> EntryDraft:
    """Row 4: Dr 1000 Bank, Cr 1010 Till."""
    return _draft(
        d, "Cash deposited to bank", EntrySource.BANK, ref, dr(BANK, cents), cr(TILL, cents)
    )


def invoice_issued(d: date, ref: str, cents: int, number: str) -> EntryDraft:
    """Row 5: Dr 1100 AR, Cr 4000 Sales on the issue date (BKS-09)."""
    return _draft(
        d, f"Invoice {number} issued", EntrySource.INVOICE, ref, dr(AR, cents), cr(SALES, cents)
    )


def customer_payment(
    d: date, ref: str, cents: int, number: str, source: EntrySource = EntrySource.BANK
) -> EntryDraft:
    """Row 6: Dr 1000 Bank, Cr 1100 AR."""
    return _draft(d, f"Payment of {number}", source, ref, dr(BANK, cents), cr(AR, cents))


def bill_received(d: date, ref: str, cents: int, number: str, category: str) -> EntryDraft:
    """Row 7: Dr expense per bill.category, Cr 2000 AP on the issue date (accrual, BKS-10)."""
    return _draft(
        d, f"Bill {number} received", EntrySource.BILL, ref, dr(category, cents), cr(AP, cents)
    )


def bill_payment(
    d: date, ref: str, cents: int, number: str, source: EntrySource = EntrySource.BANK
) -> EntryDraft:
    """Row 8: Dr 2000 AP, Cr 1000 Bank."""
    return _draft(d, f"Payment of {number}", source, ref, dr(AP, cents), cr(BANK, cents))


def bank_categorised(d: date, ref: str, signed_cents: int, label: str, memo: str) -> EntryDraft:
    """Rows 9, 10, 13 and inflows: outflow (signed < 0) Dr label / Cr 1000; inflow
    (signed > 0) Dr 1000 / Cr label. PERSONAL posts to 3100 Owner's drawings (row 10),
    never to an expense account (BKS-08)."""
    code = DRAWINGS if label == PERSONAL_LABEL else label
    cents = abs(signed_cents)
    if signed_cents < 0:
        return _draft(d, memo, EntrySource.BANK, ref, dr(code, cents), cr(BANK, cents))
    return _draft(d, memo, EntrySource.BANK, ref, dr(BANK, cents), cr(code, cents))


def refund_claim(d: date, ref: str, cents: int, memo: str) -> EntryDraft:
    """Row 11: duplicate charge, owner claims a refund: Dr 1110, Cr 1000."""
    return _draft(d, memo, EntrySource.BANK, ref, dr(REFUND_CLAIMS, cents), cr(BANK, cents))


def till_payment(d: date, ref: str, cents: int, category: str, memo: str) -> EntryDraft:
    """Row 12: cash receipt paid from the till: Dr expense per category, Cr 1010."""
    return _draft(d, memo, EntrySource.RECEIPT, ref, dr(category, cents), cr(TILL, cents))


def opening(d: date, lines: list[JournalLineDTO]) -> EntryDraft:
    """Row 14: opening balances (1000, 1010, 1500 ... / 3000 Owner's capital)."""
    validate(lines)
    return EntryDraft(d, "Opening balances", EntrySource.OPENING, "opening", tuple(lines))


def reversal(original: JournalEntryDTO, d: date, memo: str) -> EntryDraft:
    """Row 16: mirror of the original (its debits become credits and vice versa)."""
    lines = tuple(
        JournalLineDTO(
            account_code=ln.account_code, debit_cents=ln.credit_cents, credit_cents=ln.debit_cents
        )
        for ln in original.lines
    )
    validate(lines)
    return EntryDraft(d, memo, EntrySource.REVERSAL, original.id, lines, reversal_of=original.id)
