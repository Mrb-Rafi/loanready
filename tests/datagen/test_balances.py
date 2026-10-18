"""T-DGN-04: running bank balance never below 300.00; closing targets met; every
``balance_after_cents`` consistent (PRD/10 §5)."""

from __future__ import annotations

from typing import Any


def _opening_bank(bundle: Any) -> int:
    return next(ln.debit_cents for ln in bundle.opening_balances if ln.account_code == "1000")


def test_balance_after_is_running_sum(bundle: Any) -> None:
    """T-DGN-04: balance_after = opening + cumulative signed amounts, in list order."""
    running = _opening_bank(bundle)
    for t in bundle.bank_transactions:
        running += t.amount_cents
        assert t.balance_after_cents == running, t.id


def test_never_below_floor(bundle: Any) -> None:
    """T-DGN-04: opening and every balance_after >= 300.00."""
    assert _opening_bank(bundle) >= 30000
    low = min(t.balance_after_cents for t in bundle.bank_transactions)
    assert low >= 30000


def test_closing_targets(bundle: Any) -> None:
    """T-DGN-04: bank 1,900.00 (last balance_after = account balance) and till 300.00."""
    from datagen.expected import till_closing_cents

    assert bundle.bank_accounts[0].balance_cents == 190000
    assert bundle.bank_transactions[-1].balance_after_cents == 190000
    assert till_closing_cents(bundle) == 30000


def test_sandbox_reset_opening_matches(bundle: Any) -> None:
    """T-DGN-04 / PRD/04 admin reset: opening txn = balance - sum(seed txns) = opening 1000."""
    total = sum(t.amount_cents for t in bundle.bank_transactions)
    assert bundle.bank_accounts[0].balance_cents - total == _opening_bank(bundle)


def test_opening_entry_balances(bundle: Any) -> None:
    """T-DGN-04 / BKS row 14: 1000 + 1010 200.00 + 1500 8,000.00 = 3000 capital."""
    lines = {ln.account_code: ln for ln in bundle.opening_balances}
    assert lines["1010"].debit_cents == 20000
    assert lines["1500"].debit_cents == 800000
    debits = sum(ln.debit_cents for ln in bundle.opening_balances)
    credits = sum(ln.credit_cents for ln in bundle.opening_balances)
    assert debits == credits == lines["3000"].credit_cents


def test_inflows_before_outflows_within_a_day(bundle: Any) -> None:
    """T-DGN-04: inside each day inflows come first, so end-of-day checks bound every row."""
    by_day: dict[Any, list[int]] = {}
    for t in bundle.bank_transactions:
        by_day.setdefault(t.date, []).append(t.amount_cents)
    for amounts in by_day.values():
        signs = [a > 0 for a in amounts]
        assert signs == sorted(signs, reverse=True)


def test_card_settlements_net_of_fee(bundle: Any) -> None:
    """T-DGN-04 / PRD/10 §3: settlement = card sales of the previous day - 1.5% fee."""
    from datetime import date
    from decimal import Decimal

    from contracts.money import from_cents, to_cents

    card: dict[date, int] = {}
    for s in bundle.pos_sales:
        if s.tender == "card":
            card[s.ts.date()] = card.get(s.ts.date(), 0) + s.amount_cents
    txns = bundle.bank_transactions
    settlements = [t for t in txns if t.description.startswith("CARD SETTLEMENT")]
    assert settlements
    for t in settlements:
        sale_day = date.fromisoformat(t.description.split()[2])
        gross = card[sale_day]
        fee = to_cents(from_cents(gross) * Decimal("0.015"))
        assert t.amount_cents == gross - fee
        assert f"GROSS {from_cents(gross)} FEE {from_cents(fee)}" in t.description
