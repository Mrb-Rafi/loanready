"""T-BKS-05 proves PRD-08, BKS-08, CTL-FR-06: personal spending goes to the owner's
confirm queue; once confirmed it is in 3100 Owner's drawings and not in expenses."""

from __future__ import annotations

import importlib.util
from datetime import date
from typing import Any

import pytest

from contracts.enums import ConfirmKind
from contracts.errors import InvalidParams, PermissionDenied
from tests.books.helpers import txn

PERSONAL = [
    txn("t1", date(2026, 9, 26), -8640, "FRESHMART SUPERSTORE"),
    txn("t2", date(2026, 10, 3), -2400, "CINEPLEX RIVERTON"),
    txn("t3", date(2026, 10, 5), -1500, "MOBILE TOPUP 0199"),
]


def _drawings_and_expenses(books: Any) -> tuple[int, int]:
    pnl = books.profit_and_loss(date(2026, 9, 23), date(2026, 10, 14))
    bs = books.balance_sheet(date(2026, 10, 14))
    drawings = next(ln for ln in bs.lines if ln.key == "3100").values_cents["2026-10-14"]
    expenses = sum(
        ln.values_cents["total"]
        for ln in pnl.lines
        if ln.key.startswith("opex_") or ln.key == "cogs"
    )
    return drawings, expenses


def test_three_personal_items_then_drawings(books: Any, tiny: Any, owner: Any) -> None:
    """T-BKS-05: three PERSONAL items; after confirming, 125.40 is in 3100, not expenses."""
    books.load_bundle(tiny)
    books.ingest_bank(PERSONAL)
    books.categorise_pending(None)
    books.post_ready()
    queue = books.confirm_queue()
    assert [i.kind for i in queue] == [ConfirmKind.PERSONAL] * 3
    _, expenses_before = _drawings_and_expenses(books)
    for item in queue:
        books.resolve(item.id, "PERSONAL", owner)
    drawings, expenses_after = _drawings_and_expenses(books)
    assert drawings == -12540
    assert expenses_after == expenses_before
    assert books.confirm_queue() == []


def test_owner_may_override_to_expense(books: Any, tiny: Any, owner: Any) -> None:
    """T-BKS-05 / BKS-08: the owner can say "business expense" instead."""
    books.load_bundle(tiny)
    books.ingest_bank(PERSONAL[:1])
    books.categorise_pending(None)
    item = books.confirm_queue()[0]
    books.resolve(item.id, "6900", owner)
    drawings, _ = _drawings_and_expenses(books)
    assert drawings == 0


def test_only_humans_resolve(books: Any, tiny: Any, agent_principal: Any, owner: Any) -> None:
    """T-BKS-05 / BKS-15: the agent cannot confirm; invalid choices are refused."""
    books.load_bundle(tiny)
    books.ingest_bank(PERSONAL[:1])
    books.categorise_pending(None)
    item = books.confirm_queue()[0]
    with pytest.raises(PermissionDenied):
        books.resolve(item.id, "PERSONAL", agent_principal)
    with pytest.raises(InvalidParams):
        books.resolve(item.id, "1000", owner)
    books.resolve(item.id, "PERSONAL", owner)
    with pytest.raises(InvalidParams):
        books.resolve(item.id, "PERSONAL", owner)


@pytest.mark.skipif(importlib.util.find_spec("datagen") is None, reason="WP8 absent")
def test_rina_s1_exactly_three_personal(rina_books: Any) -> None:
    """T-BKS-05 on the seeded bakery (S1): exactly three PERSONAL confirm items."""
    kinds = [i.kind for i in rina_books.confirm_queue()]
    assert kinds.count(ConfirmKind.PERSONAL) == 3
