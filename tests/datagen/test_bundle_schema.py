"""T-DGN-02: the generated bundle validates as ``contracts.seed.SeedBundle`` (CON-21)."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from contracts.seed import SeedBundle


def test_bundle_file_validates(written: dict[str, Path]) -> None:
    """T-DGN-02 proves CON-21: var/seed/bundle.json parses as SeedBundle."""
    text = written["bundle"].read_text(encoding="utf-8")
    bundle = SeedBundle.model_validate_json(text)
    assert bundle.version == "1"
    assert bundle.as_of == date(2026, 10, 14)


def test_profile_and_history_window(bundle: Any) -> None:
    """T-DGN-02: PRD/10 §2 profile; history 2026-04-14 to 2026-10-13 (183 days)."""
    assert bundle.business == {
        "name": "Rina's Bakery",
        "owner": "Rina",
        "currency": "USD",
        "city": "Riverton",
    }
    assert bundle.history_start == date(2026, 4, 14)
    assert bundle.as_of - bundle.history_start == timedelta(days=183)
    days = {t.date for t in bundle.bank_transactions} | {s.ts.date() for s in bundle.pos_sales}
    assert min(days) >= bundle.history_start
    assert max(days) <= bundle.as_of - timedelta(days=1)


def test_one_operating_account_and_unique_ids(bundle: Any) -> None:
    """T-DGN-02: one account "acc_operating"; ids unique within every list."""
    assert [a.id for a in bundle.bank_accounts] == ["acc_operating"]
    for name in ("bank_transactions", "pos_sales", "invoices", "bills", "counterparties"):
        ids = [x.id for x in getattr(bundle, name)]
        assert len(ids) == len(set(ids)), name
    assert len({r["id"] for r in bundle.receipts}) == len(bundle.receipts)
    txns = bundle.bank_transactions
    assert all(t.account_id == "acc_operating" and t.kind == "seed" for t in txns)


def test_payees_match_bank_registry(bundle: Any) -> None:
    """T-DGN-02: app payee master and MockBank registry share ids and accounts (PRD/10 §2)."""
    app = {p.id: p.bank_account_ref for p in bundle.payees}
    bank = {p.id: p.bank_account_ref for p in bundle.bank_payees}
    assert (
        app
        == bank
        == {
            "pye_golden": "SBX-GM-00412277",
            "pye_boxco": "SBX-BX-00981133",
            "pye_maple": "SBX-MP-00230045",
        }
    )
    golden = next(p for p in bundle.payees if p.id == "pye_golden")
    assert golden.first_paid_on == date(2026, 5, 2)


def test_documents_reference_known_parties(bundle: Any) -> None:
    """T-DGN-02: every invoice customer, bill supplier and payee exists."""
    cpts = {c.id for c in bundle.counterparties}
    payees = {p.id for p in bundle.payees}
    assert {i.customer_id for i in bundle.invoices} <= cpts
    assert {b.supplier_id for b in bundle.bills} <= cpts
    assert {b.payee_id for b in bundle.bills if b.payee_id} <= payees


def test_pos_sales_add_up(bundle: Any) -> None:
    """T-DGN-02 / PRD/10 §3: amount = sum(qty x unit); 25-60 sales of 4.00-25.00 a day."""
    per_day: dict[date, int] = {}
    for s in bundle.pos_sales:
        assert s.amount_cents == sum(i.qty * i.unit_cents for i in s.items)
        assert 400 <= s.amount_cents <= 2500
        assert s.ts.tzinfo is not None
        per_day[s.ts.date()] = per_day.get(s.ts.date(), 0) + 1
    assert len(per_day) == 183
    assert all(25 <= n <= 60 for n in per_day.values())
    cash = sum(s.tender == "cash" for s in bundle.pos_sales)
    assert 35 <= 100 * cash // len(bundle.pos_sales) <= 45
