"""Cash-flow builders, one per ``ProductKind`` (PRD/07 §3).

Borrower perspective: ``+`` = money received, ``-`` = money paid; day 0 = AS_OF. ``A`` is the
requested amount, ``T`` the needed term in days. Maths runs on ``Decimal`` currency units and
is rounded to int cents (HALF_EVEN, ``contracts.money.to_cents``) only when a
``CashFlowPoint`` is produced; the IRR then uses those rounded cents, so the displayed flows
and the rate agree.

Every builder returns a ``Built``: the flows (empty when they cannot be computed), the
ineligibility reasons (``"CODE: text"``) and the flags. Ineligible options keep any flows
that can be computed so the UI can still show what the product would cost.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal

from contracts.dto_financing import CashFlowPoint, EligibleInvoice, FinancingRequest, ProductDTO
from contracts.enums import ProductKind
from contracts.money import MONEY_CTX, fmt, from_cents, to_cents
from financing.catalogue import param

# Flags (PRD/07 §6 step 5; HIGH_COST and the CN cap flag are set in compare.py).
TERM_LONGER_THAN_NEED = "TERM_LONGER_THAN_NEED"
TERM_SHORTER_THAN_NEED = "TERM_SHORTER_THAN_NEED"
FACTOR_RATE_PRODUCT = "FACTOR_RATE_PRODUCT"
BELOW_MIN_AMOUNT = "BELOW_MIN_AMOUNT"
ADVANCE_EXCEEDS_NEED = "ADVANCE_EXCEEDS_NEED"

# Ineligibility reason codes (prefix of each reason string).
AMOUNT_BELOW_MIN = "AMOUNT_BELOW_MIN"
AMOUNT_ABOVE_MAX = "AMOUNT_ABOVE_MAX"
ABOVE_OVERDRAFT_LIMIT = "ABOVE_OVERDRAFT_LIMIT"
TERM_ABOVE_MAX = "TERM_ABOVE_MAX"
NO_SUPPLIER_TERMS = "NO_SUPPLIER_TERMS"
NO_INVOICE_COVERAGE = "NO_INVOICE_COVERAGE"
NO_CARD_SALES = "NO_CARD_SALES"

_ONE = Decimal(1)
_DAYS_PER_FEE_PERIOD = 30
_DAYS_PER_MONTH = 30


@dataclass
class Built:
    flows: list[CashFlowPoint] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    payment_frequency: str = "once"
    chosen_invoice_ids: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.reasons and bool(self.flows)


def _point(day: int, units: Decimal) -> CashFlowPoint:
    return CashFlowPoint(day=day, amount_cents=to_cents(units))


def _amount_range(product: ProductDTO, amount_cents: int, built: Built) -> None:
    """Common eligibility: ``min_amount <= A <= max_amount``."""
    if amount_cents < product.min_amount_cents:
        built.reasons.append(
            f"{AMOUNT_BELOW_MIN}: {fmt(amount_cents)} is below the product minimum "
            f"{fmt(product.min_amount_cents)}."
        )
    if amount_cents > product.max_amount_cents:
        built.reasons.append(
            f"{AMOUNT_ABOVE_MAX}: {fmt(amount_cents)} is above the product maximum "
            f"{fmt(product.max_amount_cents)}."
        )


def build_supplier_credit(product: ProductDTO, request: FinancingRequest) -> Built:
    """Supplier trade credit; with a discount ``d`` within ``K`` days it prices the cost of
    forgoing the discount.

    Flows: ``(0, +A*(1-d))``, ``(net_days - K, -A)``. With no discount (d = 0, K = 0) this is
    ``(0, +A)``, ``(net_days, -A)`` and costs nothing. Requires
    ``supplier_terms_available``.
    """
    built = Built()
    a = from_cents(request.amount_cents)
    d = param(product, "discount_rate")
    k = int(param(product, "discount_days"))
    net = int(param(product, "net_days"))
    if not request.supplier_terms_available:
        built.reasons.append(f"{NO_SUPPLIER_TERMS}: the supplier has not offered credit terms.")
    _amount_range(product, request.amount_cents, built)
    built.flows = [
        _point(0, MONEY_CTX.multiply(a, _ONE - d)),
        _point(net - k, -a),
    ]
    return built


def build_overdraft(product: ProductDTO, request: FinancingRequest) -> Built:
    """Overdraft at APR ``a`` with fee ``f`` and limit ``L``, used for ``T`` days.

    Flows: ``(0, +A)``, ``(T, -(A + A*a*T/365 + f))``; simple interest, actual/365.
    Ineligible if ``A > L`` or ``T`` exceeds the product's maximum term.
    """
    built = Built()
    a = from_cents(request.amount_cents)
    apr = param(product, "apr")
    fee = from_cents(int(param(product, "fee_cents")))
    limit = int(param(product, "limit_cents"))
    t = request.term_days
    _amount_range(product, request.amount_cents, built)
    if request.amount_cents > limit:
        built.reasons.append(
            f"{ABOVE_OVERDRAFT_LIMIT}: {fmt(request.amount_cents)} is above the overdraft "
            f"limit {fmt(limit)}."
        )
    if t > product.max_term_days:
        built.reasons.append(
            f"{TERM_ABOVE_MAX}: {t} days is longer than the maximum {product.max_term_days} days."
        )
    interest = MONEY_CTX.divide(MONEY_CTX.multiply(MONEY_CTX.multiply(a, apr), t), 365)
    interest_cents = to_cents(interest)
    built.flows = [
        _point(0, a),
        CashFlowPoint(day=t, amount_cents=-(request.amount_cents + interest_cents + to_cents(fee))),
    ]
    return built


def amortising_payment(principal: Decimal, monthly_rate: Decimal, months: int) -> Decimal:
    """Level instalment ``pmt = P*i / (1 - (1+i)**-n)``, or ``P/n`` when ``i = 0``; unrounded."""
    if monthly_rate == 0:
        return MONEY_CTX.divide(principal, months)
    denom = MONEY_CTX.subtract(_ONE, MONEY_CTX.power(MONEY_CTX.add(_ONE, monthly_rate), -months))
    return MONEY_CTX.divide(MONEY_CTX.multiply(principal, monthly_rate), denom)


def build_bank_loan(product: ProductDTO, request: FinancingRequest) -> Built:
    """Amortising term loan: APR ``a``, ``n`` monthly instalments, origination fee rate ``o``.

    Flows: ``(0, +A*(1-o))``, then ``n`` instalments of ``pmt = A*i/(1-(1+i)**-n)``,
    ``i = a/12`` (pmt rounded to cents) at days 30, 60, ..., 30n. Each month's interest is
    ``balance*i`` rounded to cents and principal is ``pmt - interest``; the last instalment
    repays the remaining balance (the rounding residual) so total principal = A exactly.
    Flags ``TERM_LONGER_THAN_NEED`` if ``30n > T`` and ``BELOW_MIN_AMOUNT`` if ``A < min``.
    """
    built = Built(payment_frequency="monthly")
    a = from_cents(request.amount_cents)
    apr = param(product, "apr")
    n = int(param(product, "months"))
    o = param(product, "origination_fee_rate")
    _amount_range(product, request.amount_cents, built)
    if request.amount_cents < product.min_amount_cents:
        built.flags.append(BELOW_MIN_AMOUNT)
    if _DAYS_PER_MONTH * n > request.term_days:
        built.flags.append(TERM_LONGER_THAN_NEED)
    i = MONEY_CTX.divide(apr, 12)
    pmt_cents = to_cents(amortising_payment(a, i, n))
    flows = [_point(0, MONEY_CTX.multiply(a, _ONE - o))]
    balance_cents = request.amount_cents
    for month in range(1, n + 1):
        interest_cents = to_cents(MONEY_CTX.multiply(from_cents(balance_cents), i))
        if month == n:
            principal_cents = balance_cents
        else:
            principal_cents = min(pmt_cents - interest_cents, balance_cents)
        balance_cents -= principal_cents
        flows.append(
            CashFlowPoint(
                day=_DAYS_PER_MONTH * month, amount_cents=-(principal_cents + interest_cents)
            )
        )
    built.flows = flows
    return built


def choose_invoices(
    invoices: list[EligibleInvoice], needed_cents: Decimal
) -> list[EligibleInvoice] | None:
    """Smallest set of invoices, largest first, with sum(outstanding) >= ``needed_cents``.

    Ties on amount break by ``invoice_id`` (deterministic). ``None`` if all invoices together
    do not cover the need.
    """
    usable = sorted(
        (inv for inv in invoices if inv.outstanding_cents > 0),
        key=lambda inv: (-inv.outstanding_cents, inv.invoice_id),
    )
    chosen: list[EligibleInvoice] = []
    total = 0
    for inv in usable:
        if total >= needed_cents:
            break
        chosen.append(inv)
        total += inv.outstanding_cents
    if total < needed_cents or not chosen:
        return None
    return chosen


def build_invoice_financing(product: ProductDTO, request: FinancingRequest) -> Built:
    """Invoice advance: advance rate ``p``, fee ``g`` per started 30 days, flat fee.

    Chooses the smallest set of eligible invoices (largest first) with
    ``inv = sum(outstanding) >= A/p``; ``advance = inv*p``; ``D = max expected_days_to_pay``
    (at least 1); ``fee = inv*g*ceil(D/30) + flat``. Flows: ``(0, +advance)``,
    ``(D, -(advance + fee))``. The advance may exceed ``A`` (flag ``ADVANCE_EXCEEDS_NEED``).
    Ineligible when no set of invoices covers ``A/p``.
    """
    built = Built()
    p = param(product, "advance_rate")
    g = param(product, "fee_rate_per_30d")
    flat_cents = int(param(product, "flat_fee_cents"))
    _amount_range(product, request.amount_cents, built)
    chosen = None
    if p > 0:
        needed = MONEY_CTX.divide(Decimal(request.amount_cents), p)
        chosen = choose_invoices(request.eligible_invoices, needed)
    if chosen is None:
        built.reasons.append(
            f"{NO_INVOICE_COVERAGE}: open invoices do not cover {fmt(request.amount_cents)} "
            f"at an advance rate of {p}."
        )
        return built
    inv = from_cents(sum(c.outstanding_cents for c in chosen))
    days = max(1, max(c.expected_days_to_pay for c in chosen))
    periods = math.ceil(Decimal(days) / _DAYS_PER_FEE_PERIOD)
    advance_cents = to_cents(MONEY_CTX.multiply(inv, p))
    fee_cents = to_cents(MONEY_CTX.multiply(MONEY_CTX.multiply(inv, g), periods)) + flat_cents
    if advance_cents > request.amount_cents:
        built.flags.append(ADVANCE_EXCEEDS_NEED)
    built.chosen_invoice_ids = [c.invoice_id for c in chosen]
    built.flows = [
        CashFlowPoint(day=0, amount_cents=advance_cents),
        CashFlowPoint(day=days, amount_cents=-(advance_cents + fee_cents)),
    ]
    return built


def build_micro_loan(product: ProductDTO, request: FinancingRequest) -> Built:
    """Flat-fee micro-loan: fee rate ``c`` for a fixed term ``T_p``.

    Flows: ``(0, +A)``, ``(T_p, -A*(1+c))``. Flags ``TERM_SHORTER_THAN_NEED`` if ``T_p < T``.
    """
    built = Built()
    a = from_cents(request.amount_cents)
    c = param(product, "flat_fee_rate")
    tp = int(param(product, "term_days"))
    _amount_range(product, request.amount_cents, built)
    if tp < request.term_days:
        built.flags.append(TERM_SHORTER_THAN_NEED)
    built.flows = [_point(0, a), _point(tp, -MONEY_CTX.multiply(a, _ONE + c))]
    return built


def build_mca(product: ProductDTO, request: FinancingRequest) -> Built:
    """Merchant cash advance: factor ``F``, holdback ``h`` of daily card sales.

    ``daily = h * avg_daily_card_sales``; ``T_mca = ceil(F*A / daily)``; flows ``(0, +A)``,
    then ``-(F*A/T_mca)`` on days 1..T_mca. Payments are rounded cumulatively,
    ``pay_k = round(k*F*A/T) - round((k-1)*F*A/T)``, so every flow is whole cents, the
    total repaid is exactly ``F*A`` (the last day absorbs the residual) and the schedule
    stays within half a cent of the equal-payment schedule the golden rates assume.
    Always flagged ``FACTOR_RATE_PRODUCT``. Ineligible without card sales.
    """
    built = Built(payment_frequency="daily", flags=[FACTOR_RATE_PRODUCT])
    a = from_cents(request.amount_cents)
    factor = param(product, "factor")
    h = param(product, "holdback_rate")
    _amount_range(product, request.amount_cents, built)
    daily = MONEY_CTX.multiply(h, from_cents(request.avg_daily_card_sales_cents))
    if request.avg_daily_card_sales_cents <= 0 or daily <= 0:
        built.reasons.append(f"{NO_CARD_SALES}: needs card sales to repay from; none recorded.")
        return built
    repay = MONEY_CTX.multiply(factor, a)
    t_mca = int(MONEY_CTX.divide(repay, daily).to_integral_value(rounding=ROUND_CEILING))
    t_mca = max(t_mca, 1)
    if t_mca > product.max_term_days:
        built.reasons.append(
            f"{TERM_ABOVE_MAX}: repayment would take {t_mca} days, longer than the maximum "
            f"{product.max_term_days} days."
        )
    flows = [_point(0, a)]
    prev = 0
    for k in range(1, t_mca + 1):
        cum = to_cents(MONEY_CTX.divide(MONEY_CTX.multiply(repay, k), t_mca))
        flows.append(CashFlowPoint(day=k, amount_cents=-(cum - prev)))
        prev = cum
    built.flows = flows
    return built


BUILDERS: dict[ProductKind, Callable[[ProductDTO, FinancingRequest], Built]] = {
    ProductKind.SUPPLIER_CREDIT: build_supplier_credit,
    ProductKind.OVERDRAFT: build_overdraft,
    ProductKind.BANK_LOAN: build_bank_loan,
    ProductKind.INVOICE_FINANCING: build_invoice_financing,
    ProductKind.MICRO_LOAN: build_micro_loan,
    ProductKind.MERCHANT_CASH_ADVANCE: build_mca,
}


def build(product: ProductDTO, request: FinancingRequest) -> Built:
    """Dispatch to the builder for ``product.kind``."""
    return BUILDERS[product.kind](product, request)
