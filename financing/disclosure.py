"""Commercial-financing disclosure elements per option (PRD/07 §5, FIN-03, CTL-FIN-02).

Presents the elements of California SB 1235 / 10 CCR §§ 900-956 and the New York Commercial
Finance Disclosure Law (23 NYCRR 600), and the annualised rate PBoC Announcement [2021]
No. 3 asks lenders to display. Designed around these regimes; it applies to providers, and
LoanReady shows the same elements so the owner can compare options.
"""

from __future__ import annotations

from decimal import Decimal

from contracts.dto_financing import CashFlowPoint, DisclosureDTO, ProductDTO
from contracts.enums import ProductKind
from contracts.money import pct
from financing.catalogue import param
from financing.metrics import FlowMetrics

APR_LABEL = "Annualised cost (APR, estimated)"
EAR_LABEL = "Effective annual rate (compounded)"
SIMPLE_LABEL = "Simple annualised cost (simple, not compounded)"
PREPAYMENT_DEFAULT = "Not specified in the illustrative catalogue"
COLLATERAL_DEFAULT = "None stated"
DEFAULT_REGIMES = ["REG-LND-01", "REG-LND-02", "REG-LND-03", "REG-LND-04"]


def build_disclosure(
    flows: list[CashFlowPoint],
    m: FlowMetrics | None,
    payment_frequency: str,
    regimes: list[str] | None = None,
) -> DisclosureDTO:
    """Disclosure for one option.

    - funding provided = amount actually received (after any fee deducted up front)
    - finance charge = total cost = total repaid - funding provided
    - payment amount = the first scheduled payment; number of payments = count of ``-`` flows
    With no computable flows (an ineligible option) every amount is 0 and rates are ``None``.
    """
    regs = list(regimes if regimes is not None else DEFAULT_REGIMES)
    payments = [f for f in flows if f.amount_cents < 0]
    if m is None or not payments:
        return DisclosureDTO(
            funding_provided_cents=0,
            finance_charge_cents=0,
            total_repayment_cents=0,
            estimated_apr=None,
            ear=None,
            payment_amount_cents=0,
            payment_frequency="n/a",
            number_of_payments=0,
            term_days=0,
            prepayment_note=PREPAYMENT_DEFAULT,
            collateral_note=COLLATERAL_DEFAULT,
            regimes=regs,
        )
    return DisclosureDTO(
        funding_provided_cents=m.amount_received_cents,
        finance_charge_cents=m.total_cost_cents,
        total_repayment_cents=m.total_repaid_cents,
        estimated_apr=m.nominal_apr,
        ear=m.ear,
        payment_amount_cents=-payments[0].amount_cents,
        payment_frequency=payment_frequency,
        number_of_payments=len(payments),
        term_days=m.term_days,
        prepayment_note=PREPAYMENT_DEFAULT,
        collateral_note=COLLATERAL_DEFAULT,
        regimes=regs,
    )


def _factor_str(factor: Decimal) -> str:
    return f"{factor.quantize(Decimal('0.01'))}"


def mca_explanation(product: ProductDTO, term_days: int, apr: Decimal, ear: Decimal) -> str:
    """The plain-language factor-rate line required for MCAs (FIN-03), e.g.

    "A factor rate of 1.20 means repaying 1.20 times the amount. Repaid over about 90 days,
    that equals an estimated APR of 151% (EAR 352%)."
    """
    if product.kind is not ProductKind.MERCHANT_CASH_ADVANCE:
        raise ValueError("mca_explanation applies to merchant cash advances only")
    f = _factor_str(param(product, "factor"))
    return (
        f"A factor rate of {f} means repaying {f} times the amount. Repaid over about "
        f"{term_days} days, that equals an estimated APR of {pct(apr, 0)} (EAR {pct(ear, 0)})."
    )
