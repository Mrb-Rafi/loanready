"""Duplicate detection (PRD/05 §5, BKS-13, CTL-FR-05).

Two raw rows are duplicate suspects when they have the same source and amount, the same
normalised counterparty (or description token Jaccard >= 0.8), dates at most 2 days apart,
and neither is matched to a document. The later row is the suspect. A receipt and a bank
line for the same purchase are never duplicates: they have different sources (BKS-14).
"""

from __future__ import annotations

from decimal import Decimal

from books.repo import RawRow
from books.similarity import jaccard
from contracts.enums import RawStatus

MAX_DAYS = 2
SIMILAR = Decimal("0.8")
DOCUMENT_MATCH_METHOD = "MATCH"


def _same_party(a: RawRow, b: RawRow) -> bool:
    hints = a.counterparty_hint and b.counterparty_hint
    if hints and jaccard(a.counterparty_hint, b.counterparty_hint) == 1:
        return True
    return jaccard(a.description, b.description) >= SIMILAR


def is_duplicate(later: RawRow, earlier: RawRow) -> bool:
    if later.id == earlier.id or later.source != earlier.source:
        return False
    if DOCUMENT_MATCH_METHOD in (later.method, earlier.method):
        return False
    if later.amount_cents != earlier.amount_cents:
        return False
    if abs((later.date - earlier.date).days) > MAX_DAYS:
        return False
    return _same_party(later, earlier)


def find_original(row: RawRow, earlier: list[RawRow]) -> RawRow | None:
    """The earliest earlier row that ``row`` duplicates, if any. Rows already flagged as
    duplicates are not used as originals."""
    for e in earlier:
        if e.status == RawStatus.DUPLICATE_SUSPECT:
            continue
        if is_duplicate(row, e):
            return e
    return None
