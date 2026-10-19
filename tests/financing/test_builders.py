"""Cash-flow builders: every kind, rounding residuals, eligibility, zero-cost property."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.enums import ProductKind
from financing import builders
from financing.metrics import measure

ZERO_COST_PARAMS: dict[ProductKind, dict[str, str]] = {
    ProductKind.SUPPLIER_CREDIT: {"net_days": "30", "discount_rate": "0", "discount_days": "0"},
    ProductKind.OVERDRAFT: {"apr": "0", "fee_cents": "0", "limit_cents": "100000000"},
    ProductKind.BANK_LOAN: {"apr": "0", "months": "12", "origination_fee_rate": "0"},
    ProductKind.INVOICE_FINANCING: {
        "advance_rate": "0.85",
        "fee_rate_per_30d": "0",
        "flat_fee_cents": "0",
    },
    ProductKind.MICRO_LOAN: {"flat_fee_rate": "0", "term_days": "30"},
    ProductKind.MERCHANT_CASH_ADVANCE: {"factor": "1", "holdback_rate": "0.15"},
}


@settings(max_examples=40, deadline=None)
@given(
    kind=st.sampled_from(list(ProductKind)),
    amount=st.integers(min_value=1, max_value=5_000_000),
    term=st.integers(min_value=1, max_value=365),
    card=st.integers(min_value=1_000, max_value=500_000),
)
def test_zero_cost_property(h, kind: ProductKind, amount: int, term: int, card: int) -> None:
    """T-FIN-04 proves FIN-02: all fees/rates 0 (factor 1) -> flows sum to 0 and EAR = 0."""
    p = h.product(kind, ZERO_COST_PARAMS[kind])
    req = h.req(
        amount_cents=amount,
        term_days=term,
        avg_daily_card_sales_cents=card,
        eligible_invoices=[h.invoice("INV-A", amount * 2, 20)],
    )
    b = builders.build(p, req)
    assert b.flows
    assert sum(f.amount_cents for f in b.flows) == 0
    m = measure(b.flows)
    assert m.ear == 0
    assert m.total_cost_cents == 0


def test_bank_loan_residual_and_fee(h) -> None:
    """T-FIN-05 proves FIN-02: principal sums to A; fee deducted up front; flags."""
    p = h.product(
        ProductKind.BANK_LOAN,
        {"apr": "0.12", "months": "12", "origination_fee_rate": "0.01"},
        min_amount_cents=200000,
    )
    b = builders.build(p, h.req(amount_cents=1_000_000, term_days=60))
    assert b.flows[0].amount_cents == 990000
    assert [f.day for f in b.flows[1:]] == [30 * k for k in range(1, 13)]
    assert b.payment_frequency == "monthly"
    assert builders.TERM_LONGER_THAN_NEED in b.flags
    small = builders.build(p, h.req(amount_cents=100000, term_days=360))
    assert builders.BELOW_MIN_AMOUNT in small.flags
    assert any(r.startswith(builders.AMOUNT_BELOW_MIN) for r in small.reasons)
    assert not small.eligible


def test_overdraft_limit_and_term(h) -> None:
    """T-FIN-05: overdraft ineligible above its limit or beyond its max term; fee added."""
    p = h.product(
        ProductKind.OVERDRAFT,
        {"apr": "0.18", "fee_cents": "1000", "limit_cents": "500000"},
        max_term_days=365,
    )
    ok = builders.build(p, h.req(amount_cents=100000, term_days=30))
    assert ok.eligible
    assert ok.flows[-1].amount_cents == -(100000 + 1479 + 1000)
    over = builders.build(p, h.req(amount_cents=600000))
    assert any(r.startswith(builders.ABOVE_OVERDRAFT_LIMIT) for r in over.reasons)
    long = builders.build(p, h.req(amount_cents=100000, term_days=400))
    assert any(r.startswith(builders.TERM_ABOVE_MAX) for r in long.reasons)


def test_invoice_financing_selection(h) -> None:
    """T-FIN-05: smallest set, largest first, covering A/p; fee per started 30 days."""
    p = h.product(
        ProductKind.INVOICE_FINANCING,
        {"advance_rate": "0.85", "fee_rate_per_30d": "0.02", "flat_fee_cents": "2500"},
    )
    invs = [
        h.invoice("INV-1", 40000, 10),
        h.invoice("INV-2", 70000, 45),
        h.invoice("INV-3", 70000, 20),
    ]
    # need A/p = 100,000 / 0.85 = 117,647.06 cents -> INV-2 + INV-3 (ties by id) = 140,000
    b = builders.build(p, h.req(amount_cents=100000, eligible_invoices=invs))
    assert b.eligible
    assert b.chosen_invoice_ids == ["INV-2", "INV-3"]
    advance = 119000
    fee = 140000 * 2 // 100 * 2 + 2500  # 2 started 30-day periods (D = 45)
    assert [(f.day, f.amount_cents) for f in b.flows] == [(0, advance), (45, -(advance + fee))]
    assert builders.ADVANCE_EXCEEDS_NEED in b.flags


def test_invoice_financing_no_coverage(h) -> None:
    """T-FIN-05: ineligible when open invoices do not cover A/p."""
    p = h.product(
        ProductKind.INVOICE_FINANCING,
        {"advance_rate": "0.85", "fee_rate_per_30d": "0.02", "flat_fee_cents": "2500"},
    )
    b = builders.build(
        p, h.req(amount_cents=100000, eligible_invoices=[h.invoice("INV-1", 50000, 10)])
    )
    assert not b.eligible
    assert b.flows == []
    assert b.reasons[0].startswith(builders.NO_INVOICE_COVERAGE)


def test_mca_needs_card_sales(h) -> None:
    """T-FIN-05: MCA ineligible without card sales; always factor-rate flagged."""
    p = h.product(ProductKind.MERCHANT_CASH_ADVANCE, {"factor": "1.20", "holdback_rate": "0.15"})
    b = builders.build(p, h.req(avg_daily_card_sales_cents=0))
    assert not b.eligible
    assert b.reasons[0].startswith(builders.NO_CARD_SALES)
    assert builders.FACTOR_RATE_PRODUCT in b.flags


def test_mca_daily_cents_schedule(h) -> None:
    """T-FIN-05: MCA payments are whole cents within 1 cent of each other; total = F x A."""
    p = h.product(ProductKind.MERCHANT_CASH_ADVANCE, {"factor": "1.20", "holdback_rate": "0.15"})
    b = builders.build(p, h.req(amount_cents=100000, avg_daily_card_sales_cents=8889))
    pays = [-f.amount_cents for f in b.flows[1:]]
    assert len(pays) == 90
    assert sum(pays) == 120000
    assert max(pays) - min(pays) <= 1
    assert b.payment_frequency == "daily"


def test_mca_too_long_is_ineligible(h) -> None:
    """T-FIN-05: an MCA that would take longer than the product maximum is ineligible."""
    p = h.product(
        ProductKind.MERCHANT_CASH_ADVANCE,
        {"factor": "1.20", "holdback_rate": "0.15"},
        max_term_days=540,
    )
    b = builders.build(p, h.req(amount_cents=1_000_000, avg_daily_card_sales_cents=100))
    assert any(r.startswith(builders.TERM_ABOVE_MAX) for r in b.reasons)


def test_supplier_requires_terms(h) -> None:
    """T-FIN-05: supplier credit requires supplier_terms_available."""
    p = h.product(
        ProductKind.SUPPLIER_CREDIT,
        {"net_days": "30", "discount_rate": "0", "discount_days": "0"},
    )
    b = builders.build(p, h.req(supplier_terms_available=False))
    assert not b.eligible
    assert b.reasons[0].startswith(builders.NO_SUPPLIER_TERMS)


def test_micro_loan_term_flag(h) -> None:
    """T-FIN-07: micro-loan shorter than the need is flagged TERM_SHORTER_THAN_NEED."""
    p = h.product(ProductKind.MICRO_LOAN, {"flat_fee_rate": "0.05", "term_days": "30"})
    assert builders.TERM_SHORTER_THAN_NEED in builders.build(p, h.req(term_days=45)).flags
    assert builders.TERM_SHORTER_THAN_NEED not in builders.build(p, h.req(term_days=30)).flags


def test_amount_above_max(h) -> None:
    """T-FIN-05: amount above the product maximum is ineligible."""
    p = h.product(
        ProductKind.MICRO_LOAN,
        {"flat_fee_rate": "0.05", "term_days": "30"},
        max_amount_cents=300000,
    )
    b = builders.build(p, h.req(amount_cents=300001))
    assert any(r.startswith(builders.AMOUNT_ABOVE_MAX) for r in b.reasons)
