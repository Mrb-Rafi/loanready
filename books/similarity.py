"""Name similarity used for document matching, duplicates and receipts (BKS-04, -13, -14).

Scores are ``Decimal`` in [0, 1] (no floats in the books package).
"""

from __future__ import annotations

import re
from decimal import Decimal

from contracts.untrusted import normalize_for_scan

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"co", "inc", "ltd", "llc", "the", "and"})
ZERO = Decimal(0)


def tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(t for t in _TOKEN.findall(normalize_for_scan(text)) if t not in _STOP)


def jaccard(a: str | None, b: str | None) -> Decimal:
    """|A & B| / |A | B| over normalised tokens (0 when either side is empty)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return ZERO
    return Decimal(len(ta & tb)) / Decimal(len(ta | tb))


def name_in(name: str, text: str | None) -> Decimal:
    """Share of the name's tokens found in ``text`` (bank memos carry the name plus a
    document number, e.g. "BRIGHT EVENTS CO. PAYMENT INV-0990")."""
    tn, tt = tokens(name), tokens(text)
    if not tn or not tt:
        return ZERO
    return Decimal(len(tn & tt)) / Decimal(len(tn))
