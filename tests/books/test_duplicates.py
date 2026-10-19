"""T-BKS-06, T-BKS-07 prove BKS-13, BKS-14, CTL-FR-05: duplicate suspects within 2 days
(not at 3); receipt + bank line is not a duplicate; resolution to 1110 or to expense."""

from __future__ import annotations

from datetime import date
from typing import Any

from contracts.enums import ConfirmKind, RawStatus
from tests.books.helpers import txn


def _setup(books: Any, tiny: Any, gap_days: int) -> None:
    books.load_bundle(tiny)
    books.ingest_bank(
        [
            txn("p1", date(2026, 9, 18), -12000, "PRINT SHOP PROMO", "Print Shop"),
            txn("p2", date(2026, 9, 18 + gap_days), -12000, "PRINT SHOP PROMO", "Print Shop"),
        ]
    )
    books.categorise_pending(None)
    books.post_ready()


def test_flagged_within_two_days(books: Any, tiny: Any) -> None:
    """T-BKS-06: the later of two equal charges 1 day apart is DUPLICATE_SUSPECT, unposted."""
    _setup(books, tiny, 1)
    rows = {r.source_ref: r for r in books.repo.raw_rows()}
    assert rows["p1"].status == RawStatus.POSTED
    assert rows["p2"].status == RawStatus.DUPLICATE_SUSPECT and rows["p2"].journal_entry_id is None
    queue = books.confirm_queue()
    assert [i.kind for i in queue] == [ConfirmKind.DUPLICATE]
    assert queue[0].options == ["genuine_second_purchase", "duplicate_claim_refund"]


def test_not_flagged_at_three_days(books: Any, tiny: Any) -> None:
    """T-BKS-06: 3 days apart is not a duplicate; both are posted."""
    _setup(books, tiny, 3)
    assert all(r.status == RawStatus.POSTED for r in books.repo.raw_rows())
    assert books.confirm_queue() == []


def test_refund_claim_goes_to_1110(books: Any, tiny: Any, owner: Any) -> None:
    """T-BKS-07: "duplicate_claim_refund" posts Dr 1110 / Cr 1000."""
    _setup(books, tiny, 1)
    item = books.confirm_queue()[0]
    books.resolve(item.id, "duplicate_claim_refund", owner)
    bs = books.balance_sheet(date(2026, 10, 14))
    assert next(ln for ln in bs.lines if ln.key == "1110").values_cents["2026-10-14"] == 12000


def test_genuine_goes_to_expense(books: Any, tiny: Any, owner: Any) -> None:
    """T-BKS-07: "genuine_second_purchase" posts the rule's expense account (6300)."""
    _setup(books, tiny, 1)
    item = books.confirm_queue()[0]
    books.resolve(item.id, "genuine_second_purchase", owner)
    pnl = books.profit_and_loss(date(2026, 9, 1), date(2026, 9, 30))
    assert next(ln for ln in pnl.lines if ln.key == "opex_6300").values_cents["total"] == 24000


def test_receipt_and_bank_line_not_duplicate(books: Any, tiny: Any, tmp_path: Any) -> None:
    """T-BKS-06 / BKS-14: a receipt for a bank purchase is linked, not posted twice."""
    from tests.books.test_receipts import write_receipt

    books.load_bundle(tiny)
    books.ingest_bank(
        [txn("g1", date(2026, 10, 10), -6420, "GRAIN DEPOT WHOLESALE", "Grain Depot")]
    )
    books.categorise_pending(None)
    books.post_ready()
    before = len(books.journal())
    rc = books.ingest_receipt(write_receipt(tmp_path, "clean"))
    assert rc.status.value == "AUTO"
    assert len(books.journal()) == before
    assert books.confirm_queue() == []


def test_rina_s5(rina_books: Any, rina_bundle: Any) -> None:
    """T-BKS-06 on the seeded bakery (S5): the second PRINT SHOP PROMO is flagged."""
    first, second = rina_bundle.scenario_ids["S5"]
    rows = {r.source_ref: r for r in rina_books.repo.raw_rows()}
    assert rows[first].status == RawStatus.POSTED
    assert rows[second].status == RawStatus.DUPLICATE_SUSPECT
