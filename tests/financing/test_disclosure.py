"""Disclosure elements for every option and the MCA explanatory line."""

from __future__ import annotations

from decimal import Decimal

import pytest

from contracts.enums import ProductKind
from financing import compare as compare_mod
from financing.disclosure import (
    APR_LABEL,
    COLLATERAL_DEFAULT,
    EAR_LABEL,
    PREPAYMENT_DEFAULT,
    mca_explanation,
)


def _rina_like(h):
    return h.req(
        amount_cents=120000,
        term_days=21,
        avg_daily_card_sales_cents=15000,
        eligible_invoices=[h.invoice("INV-1", 80000, 25), h.invoice("INV-2", 90000, 14)],
    )


def test_disclosures_complete_for_every_option(cat, params, h) -> None:
    """T-FIN-08 proves REG-LND-01..04, CTL-FIN-02: every computed option discloses funding,
    finance charge, total repayment, APR, EAR, payments, term, prepayment and collateral."""
    result = compare_mod.compare(cat, params, _rina_like(h))
    assert len(result.options) == len(cat.products)
    for o in result.options:
        d = o.disclosure
        assert d.regimes == ["REG-LND-01", "REG-LND-02", "REG-LND-03", "REG-LND-04"]
        assert d.prepayment_note == PREPAYMENT_DEFAULT
        assert d.collateral_note == COLLATERAL_DEFAULT
        if not o.flows:
            assert d.estimated_apr is None and d.ear is None
            continue
        assert d.funding_provided_cents == o.amount_received_cents > 0
        assert d.finance_charge_cents == o.total_cost_cents
        assert d.total_repayment_cents == o.total_repaid_cents
        assert d.total_repayment_cents - d.funding_provided_cents == d.finance_charge_cents
        assert d.estimated_apr == o.nominal_apr is not None
        assert d.ear == o.ear is not None
        assert d.number_of_payments == sum(1 for f in o.flows if f.amount_cents < 0)
        assert d.payment_amount_cents > 0
        assert d.term_days == o.term_days > 0
        assert d.payment_frequency in {"once", "monthly", "daily"}


def test_labels() -> None:
    """T-FIN-08 proves FIN-03: the agreed display labels."""
    assert APR_LABEL == "Annualised cost (APR, estimated)"
    assert EAR_LABEL == "Effective annual rate (compounded)"


def test_mca_explanatory_sentence(cat, params, h) -> None:
    """T-FIN-08 proves FIN-03, CTL-FIN-02: the MCA factor-rate sentence (golden case)."""
    product = next(p for p in cat.products if p.kind is ProductKind.MERCHANT_CASH_ADVANCE)
    opt = compare_mod.quote(
        cat, params, h.req(amount_cents=100000, avg_daily_card_sales_cents=8889), product.product_id
    )
    text = mca_explanation(product, opt.term_days, opt.nominal_apr, opt.ear)
    assert text == (
        "A factor rate of 1.20 means repaying 1.20 times the amount. Repaid over about 90 days, "
        "that equals an estimated APR of 151% (EAR 352%)."
    )
    result = compare_mod.compare(
        cat, params, h.req(amount_cents=100000, avg_daily_card_sales_cents=8889)
    )
    assert text in result.notices


def test_mca_explanation_rejects_other_kinds(cat) -> None:
    """T-FIN-08: the factor-rate sentence is only for MCAs."""
    other = next(p for p in cat.products if p.kind is ProductKind.MICRO_LOAN)
    with pytest.raises(ValueError):
        mca_explanation(other, 30, Decimal("0.5"), Decimal("0.8"))
