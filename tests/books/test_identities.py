"""T-BKS-03 proves BKS-19, CTL-FR-07: ledger identities 1-4 hold for random event
sequences (hypothesis); identities 1-5 hold on the seeded bakery."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from contracts.sandbox_api import PosItem, PosSaleOut
from tests.books.helpers import txn

START = date(2026, 9, 23)
AS_OF = date(2026, 10, 14)
MEMOS = ["GRAIN DEPOT WHOLESALE", "PAYROLL", "RENT - X", "NETLINK INTERNET", "CORNER HARDWARE"]


def check_identities(books: Any, start: date, end: date) -> None:
    journal = books.journal()
    # 1. total debits = total credits
    assert sum(ln.debit_cents for e in journal for ln in e.lines) == sum(
        ln.credit_cents for e in journal for ln in e.lines
    )
    # 2. A = L + E at every month end and at the end date
    d = date(start.year, start.month, 1)
    checkpoints = [end]
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        if nxt - timedelta(days=1) <= end:
            checkpoints.append(nxt - timedelta(days=1))
        d = nxt
    for c in checkpoints:
        bs = books.balance_sheet(c)
        assert bs.total_assets_cents == bs.total_liabilities_cents + bs.total_equity_cents, c
    # 3. net profit = change in profit to date
    pnl = books.profit_and_loss(start, end)
    profit = next(ln for ln in books.balance_sheet(end).lines if ln.key == "profit_to_date")
    before = next(
        ln
        for ln in books.balance_sheet(start - timedelta(days=1)).lines
        if ln.key == "profit_to_date"
    )
    assert pnl.total_net_profit_cents == (
        profit.values_cents[end.isoformat()]
        - before.values_cents[(start - timedelta(days=1)).isoformat()]
    )
    # 4. cash-flow closing = ledger cash
    cf = books.cash_flow_statement(start, end)
    assert cf.closing_cash_cents == books.cash_balance(end)


EVENTS = st.lists(
    st.tuples(
        st.integers(0, 20),  # day offset
        st.sampled_from(["pos_cash", "pos_card", "bank_out", "bank_in", "settle", "deposit"]),
        st.integers(1, 200_000),
        st.integers(0, len(MEMOS) - 1),
    ),
    min_size=1,
    max_size=25,
)


@settings(
    max_examples=15,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(evs=EVENTS)
def test_identities_random_sequences(
    evs: list[tuple[int, str, int, int]], tmp_path_factory: Any, tiny: Any
) -> None:
    """T-BKS-03: identities 1-4 after any mix of POS sales, bank lines, settlements,
    deposits, documents and owner confirmations."""
    from books.service import Books
    from contracts.actions import OWNER
    from contracts.clock import FixedClock
    from contracts.ids import IdFactory
    from infra.db import create_all, engine_for
    from infra.settings import get_settings

    eng = engine_for(Path(tmp_path_factory.mktemp("h")) / "app.db")
    create_all(eng)
    books = Books(eng, FixedClock(AS_OF), IdFactory(1), get_settings())
    books.load_bundle(tiny)
    sales, bank = [], []
    for n, (off, kind, cents, memo) in enumerate(evs):
        d = START + timedelta(days=off)
        if kind.startswith("pos"):
            sales.append(
                PosSaleOut(
                    id=f"s{n}",
                    ts=d.isoformat() + "T10:00:00Z",  # type: ignore[arg-type]
                    amount_cents=cents,
                    tender="cash" if kind == "pos_cash" else "card",
                    items=[PosItem(name="x", qty=1, unit_cents=cents)],
                )
            )
        elif kind == "settle":
            bank.append(txn(f"b{n}", d, cents, f"CARD SETTLEMENT {d}"))
        elif kind == "deposit":
            bank.append(txn(f"b{n}", d, cents, "CASH DEPOSIT"))
        else:
            sign = -1 if kind == "bank_out" else 1
            bank.append(txn(f"b{n}", d, sign * cents, MEMOS[memo]))
    books.ingest_pos(sales)
    books.ingest_bank(bank)
    books.categorise_pending(None)
    books.post_ready()
    for item in books.confirm_queue():
        choice = "duplicate_claim_refund" if item.kind.value == "DUPLICATE" else item.proposed
        books.resolve(item.id, choice, OWNER)
    check_identities(books, START, AS_OF)


def test_tiny_identities(tiny_books: Any) -> None:
    """T-BKS-03 on the tiny fixture."""
    check_identities(tiny_books, START, AS_OF)


def test_rina_identities(rina_books: Any, rina_bundle: Any) -> None:
    """T-BKS-03 / identities 1-5 on the seeded bakery (WP8 bundle)."""
    check_identities(rina_books, rina_bundle.history_start, rina_bundle.as_of)
    rec = rina_books.reconcile(
        rina_bundle.bank_transactions, rina_bundle.bank_accounts[0].balance_cents, rina_bundle.as_of
    )
    assert rec.is_reconciled
