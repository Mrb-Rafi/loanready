"""IRR, APR and EAR: sign convention, convergence and the PRD/07 §4 golden table."""

from __future__ import annotations

from decimal import Decimal

import pytest

from contracts.dto_financing import CashFlowPoint
from contracts.enums import ProductKind
from contracts.errors import InvalidParams
from financing import builders
from financing.irr import TOLERANCE, daily_irr, npv
from financing.metrics import measure

TOL = Decimal("0.0005")


def _flows(*pairs: tuple[int, int]) -> list[CashFlowPoint]:
    return [CashFlowPoint(day=d, amount_cents=c) for d, c in pairs]


def _close(actual: Decimal | None, expected: str) -> None:
    assert actual is not None
    assert abs(actual - Decimal(expected)) <= TOL, (actual, expected)


def test_sign_convention_and_convergence() -> None:
    """T-FIN-01 proves FIN-02, CTL-FIN-02: NPV(0) = -cost <= 0, NPV rises with r, root found."""
    flows = _flows((0, 100000), (30, -105000))
    assert npv(flows, Decimal(0)) == Decimal("-50.00")
    assert npv(flows, Decimal("0.001")) < npv(flows, Decimal("0.01"))
    r = daily_irr(flows)
    assert abs(npv(flows, r)) < Decimal("0.000001")
    # converged within the bisection tolerance
    assert npv(flows, r - 10 * TOLERANCE) < 0 < npv(flows, r + 10 * TOLERANCE)


def test_zero_cost_irr_is_zero() -> None:
    """T-FIN-01 proves FIN-02: zero total cost gives r = 0 exactly."""
    assert daily_irr(_flows((0, 5000), (30, -5000))) == 0


def test_irr_rejects_impossible_flows() -> None:
    """T-FIN-01: empty flows, negative cost and >100%/day cost raise InvalidParams."""
    with pytest.raises(InvalidParams):
        daily_irr([])
    with pytest.raises(InvalidParams):
        daily_irr(_flows((0, 100), (10, -50)))
    with pytest.raises(InvalidParams):
        daily_irr(_flows((0, 100), (1, -1000)))


def test_golden_bank_loan(h) -> None:
    """T-FIN-02 proves CTL-FIN-02: 10,000 at 12% APR, 12 monthly, no fee."""
    p = h.product(
        ProductKind.BANK_LOAN, {"apr": "0.12", "months": "12", "origination_fee_rate": "0"}
    )
    b = builders.build(p, h.req(amount_cents=1_000_000, term_days=360))
    assert [f.amount_cents for f in b.flows[1:-1]] == [-88849] * 11
    m = measure(b.flows)
    assert abs(m.total_cost_cents - 66185) <= 5
    _close(m.daily_irr, "0.0003317")
    _close(m.nominal_apr, "0.1211")
    _close(m.ear, "0.1287")


def test_golden_overdraft(h) -> None:
    """T-FIN-02 proves CTL-FIN-02: overdraft 1,000 at 18% for 30 days."""
    p = h.product(ProductKind.OVERDRAFT, {"apr": "0.18", "fee_cents": "0", "limit_cents": "500000"})
    m = measure(builders.build(p, h.req(amount_cents=100000, term_days=30)).flows)
    assert m.total_cost_cents == 1479
    _close(m.nominal_apr, "0.1787")
    _close(m.ear, "0.1956")


def test_golden_supplier_net30(h) -> None:
    """T-FIN-02: supplier net 30 without discount costs nothing."""
    p = h.product(
        ProductKind.SUPPLIER_CREDIT,
        {"net_days": "30", "discount_rate": "0", "discount_days": "0"},
    )
    m = measure(builders.build(p, h.req(amount_cents=100000)).flows)
    assert m.total_cost_cents == 0
    assert m.ear == 0
    assert m.nominal_apr == 0


def test_golden_supplier_2_10_net30(h) -> None:
    """T-FIN-02: forgoing 2/10 net 30 on 1,000 = receive 980, pay 1,000 on day 20."""
    p = h.product(
        ProductKind.SUPPLIER_CREDIT,
        {"net_days": "30", "discount_rate": "0.02", "discount_days": "10"},
    )
    b = builders.build(p, h.req(amount_cents=100000))
    assert [(f.day, f.amount_cents) for f in b.flows] == [(0, 98000), (20, -100000)]
    m = measure(b.flows)
    _close(m.nominal_apr, "0.3689")
    _close(m.ear, "0.4459")


def test_golden_micro_loan(h) -> None:
    """T-FIN-02: 5% flat for 30 days on 1,000."""
    p = h.product(ProductKind.MICRO_LOAN, {"flat_fee_rate": "0.05", "term_days": "30"})
    m = measure(builders.build(p, h.req(amount_cents=100000)).flows)
    assert m.total_cost_cents == 5000
    _close(m.nominal_apr, "0.5941")
    _close(m.ear, "0.8105")


def test_golden_mca(h) -> None:
    """T-FIN-03 proves FIN-04, CTL-FIN-02: MCA factor 1.20 on 1,000 over 90 days:
    simple 81.11%, APR 151.19%, EAR 352.12% (> 300%)."""
    p = h.product(ProductKind.MERCHANT_CASH_ADVANCE, {"factor": "1.20", "holdback_rate": "0.15"})
    b = builders.build(p, h.req(amount_cents=100000, avg_daily_card_sales_cents=8889))
    m = measure(b.flows)
    assert m.term_days == 90
    assert m.total_cost_cents == 20000
    _close(m.simple_annualised, "0.8111")
    _close(m.nominal_apr, "1.5119")
    _close(m.ear, "3.5212")
    assert m.ear > Decimal("3.00")
