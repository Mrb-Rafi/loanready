"""T-BKS-02 proves BKS-16..BKS-19, CTL-FR-07: tiny-fixture statements equal PRD/15 §3.1."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

AS_OF = date(2026, 10, 14)


def _line(stmt: Any, key: str) -> dict[str, int]:
    return next(ln.values_cents for ln in stmt.lines if ln.key == key)


def test_cash_ar_ap(tiny_books: Any) -> None:
    """T-BKS-02: bank 2,710.00, till 1,100.00, cash 3,810.00, AR 600.00, AP 800.00."""
    bs = tiny_books.balance_sheet(AS_OF)
    assert _line(bs, "1000")[AS_OF.isoformat()] == 271000
    assert _line(bs, "1010")[AS_OF.isoformat()] == 110000
    assert tiny_books.cash_balance(AS_OF) == 381000
    assert _line(bs, "1100")[AS_OF.isoformat()] == 60000
    assert _line(bs, "2000")[AS_OF.isoformat()] == 80000


def test_profit_and_loss(tiny_books: Any) -> None:
    """T-BKS-02: Sep revenue 1,600 COGS 300 net 1,300; Oct revenue 2,300 COGS 1,490 rent
    500 net 310; total 1,610."""
    pnl = tiny_books.profit_and_loss(date(2026, 9, 23), date(2026, 10, 13))
    assert pnl.periods == ["2026-09", "2026-10"]
    rev, cogs, gp = _line(pnl, "revenue"), _line(pnl, "cogs"), _line(pnl, "gross_profit")
    assert (rev["2026-09"], cogs["2026-09"], gp["2026-09"]) == (160000, 30000, 130000)
    assert (rev["2026-10"], cogs["2026-10"], gp["2026-10"]) == (230000, 149000, 81000)
    assert _line(pnl, "opex_6000")["2026-10"] == 50000
    assert sum(_line(pnl, f"opex_{c}")["2026-09"] for c in ("6000", "6100", "6200")) == 0
    assert pnl.net_profit_cents == {"2026-09": 130000, "2026-10": 31000}
    assert pnl.total_net_profit_cents == 161000


def test_balance_sheet(tiny_books: Any) -> None:
    """T-BKS-02: assets 4,410 = liabilities 800 + capital 2,000 + profit 1,610."""
    bs = tiny_books.balance_sheet(AS_OF)
    assert bs.total_assets_cents == 441000
    assert bs.total_liabilities_cents == 80000
    assert _line(bs, "3000")[AS_OF.isoformat()] == 200000
    assert _line(bs, "profit_to_date")[AS_OF.isoformat()] == 161000
    assert bs.total_equity_cents == 361000
    assert bs.total_assets_cents == bs.total_liabilities_cents + bs.total_equity_cents


def test_cash_flow_direct(tiny_books: Any) -> None:
    """T-BKS-02: 09-24..10-13 opening 2,000; customers 3,300; suppliers -990; rent -500;
    net operating 1,810; closing 3,810."""
    cf = tiny_books.cash_flow_statement(date(2026, 9, 24), date(2026, 10, 13))
    assert cf.method == "direct"
    assert cf.opening_cash_cents == 200000
    assert _line(cf, "receipts_customers")["total"] == 330000
    assert _line(cf, "paid_suppliers")["total"] == -99000
    assert _line(cf, "rent_paid")["total"] == -50000
    assert cf.net_operating_cents == 181000
    assert cf.net_investing_cents == 0 and cf.net_financing_cents == 0
    assert cf.closing_cash_cents == 381000 == tiny_books.cash_balance(date(2026, 10, 13))


def test_weekly_sales_and_cogs_ratio(tiny_books: Any) -> None:
    """T-BKS-02 / BKS-29, BKS-30: weekly_sales(3) 1,000 / 1,200 / 1,100; cogs_ratio 0.3000."""
    weeks = tiny_books.weekly_sales(3, AS_OF)
    assert [w.total_cents for w in weeks] == [100000, 120000, 110000]
    assert [w.week_start for w in weeks] == [
        date(2026, 9, 24),
        date(2026, 10, 1),
        date(2026, 10, 8),
    ]
    assert tiny_books.cogs_ratio(3, AS_OF) == Decimal("0.3000")


def test_tiny_nothing_left_to_confirm(tiny_books: Any) -> None:
    """T-BKS-02: every tiny bank line is categorised by rule and posted."""
    assert tiny_books.confirm_queue() == []
    assert tiny_books.reconciliation_open_items() == 0
