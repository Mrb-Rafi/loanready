"""Neutral comparison of every catalogue product for one request (PRD/07 §6, CTL-FIN-03).

Options are ranked only by annualised cost (EAR ascending; ties by total cost in cents,
then ``product_id``). There are no sponsored products, commissions or "recommended
lender". Ineligible options stay in the list with their reasons and ``rank=None``.
LoanReady compares and prepares; it never accepts or arranges a loan (REG-LND-05, 06).
"""

from __future__ import annotations

from collections.abc import Sequence

from contracts.dto_financing import (
    CatalogueDTO,
    FinancingComparisonDTO,
    FinancingOption,
    FinancingRequest,
    ProductDTO,
)
from contracts.enums import ProductKind
from contracts.errors import InvalidParams
from financing import catalogue as cat_mod
from financing.builders import build
from financing.disclosure import build_disclosure, mca_explanation
from financing.metrics import FlowMetrics, measure
from financing.regparams import RegParams

HIGH_COST = "HIGH_COST"
ABOVE_CN_PRIVATE_LENDING_CAP = "ABOVE_CN_PRIVATE_LENDING_CAP"

NOT_A_LENDER_NOTICE = (
    "LoanReady compares costs and prepares documents. It does not lend, arrange or accept loans."
)
NOT_ADVICE_NOTICE = "This is not financial advice."
CN_CAP_SKIPPED_NOTICE = (
    "China private-lending interest cap (4 x 1-year LPR) not checked: the LPR parameter is "
    "not applied in this build."
)


def illustrative_notice(catalogue: CatalogueDTO) -> str:
    return f"Illustrative products dated {catalogue.as_of.isoformat()}; not offers."


def _validate_request(request: FinancingRequest) -> None:
    if request.amount_cents <= 0:
        raise InvalidParams("financing amount must be positive", details={"field": "amount_cents"})
    if request.term_days <= 0:
        raise InvalidParams("financing term must be positive", details={"field": "term_days"})
    if request.avg_daily_card_sales_cents < 0:
        raise InvalidParams(
            "card sales cannot be negative", details={"field": "avg_daily_card_sales_cents"}
        )


def build_option(
    product: ProductDTO, request: FinancingRequest, params: RegParams
) -> tuple[FinancingOption, FlowMetrics | None]:
    """One unranked option: flows, measures, disclosure, eligibility and flags."""
    built = build(product, request)
    m = measure(built.flows) if built.flows else None
    flags = list(built.flags)
    if m is not None and m.nominal_apr > params.high_cost_flag_apr:
        flags.insert(0, HIGH_COST)
    option = FinancingOption(
        product_id=product.product_id,
        kind=product.kind,
        name=product.name,
        provider=product.provider,
        eligible=built.eligible,
        ineligibility_reasons=list(built.reasons),
        flows=list(built.flows),
        amount_received_cents=m.amount_received_cents if m else 0,
        total_repaid_cents=m.total_repaid_cents if m else 0,
        total_cost_cents=m.total_cost_cents if m else 0,
        daily_irr=m.daily_irr if m else None,
        nominal_apr=m.nominal_apr if m else None,
        ear=m.ear if m else None,
        simple_annualised=m.simple_annualised if m else None,
        term_days=m.term_days if m else 0,
        disclosure=build_disclosure(
            built.flows, m, built.payment_frequency, params.disclosure_regimes
        ),
        flags=flags,
        rank=None,
    )
    return option, m


def rank_options(options: list[FinancingOption]) -> list[FinancingOption]:
    """Set ``rank`` 1..n on eligible options by (EAR, total cost, product_id); others None."""
    eligible = sorted(
        (o for o in options if o.eligible and o.ear is not None),
        key=lambda o: (o.ear, o.total_cost_cents, o.product_id),
    )
    ranks = {o.product_id: i for i, o in enumerate(eligible, start=1)}
    return [o.model_copy(update={"rank": ranks.get(o.product_id)}) for o in options]


def compare(
    catalogue: CatalogueDTO,
    params: RegParams,
    request: FinancingRequest,
    product_ids: Sequence[str] | None = None,
) -> FinancingComparisonDTO:
    """Compare every catalogue product (or only ``product_ids``; unknown ids raise
    ``CatalogueMiss``) for ``request``."""
    _validate_request(request)
    if product_ids is None:
        products = list(catalogue.products)
    else:
        products = [cat_mod.get(catalogue, pid) for pid in product_ids]
    options: list[FinancingOption] = []
    explanations: list[str] = []
    for product in products:
        option, m = build_option(product, request, params)
        options.append(option)
        if product.kind is ProductKind.MERCHANT_CASH_ADVANCE and m is not None:
            explanations.append(mca_explanation(product, m.term_days, m.nominal_apr, m.ear))
    options = rank_options(options)
    ranked = sorted((o for o in options if o.rank is not None), key=lambda o: o.rank or 0)
    notices = [illustrative_notice(catalogue), NOT_A_LENDER_NOTICE, NOT_ADVICE_NOTICE]
    notices.append(CN_CAP_SKIPPED_NOTICE)
    notices.extend(explanations)
    return FinancingComparisonDTO(
        request=request,
        options=options,
        cheapest_id=ranked[0].product_id if ranked else None,
        most_expensive_id=ranked[-1].product_id if ranked else None,
        catalogue_as_of=catalogue.as_of,
        catalogue_source=catalogue.source,
        notices=notices,
    )


def quote(
    catalogue: CatalogueDTO, params: RegParams, request: FinancingRequest, product_id: str
) -> FinancingOption:
    """One unranked option for ``product_id``; raises ``CatalogueMiss`` if it is unknown."""
    _validate_request(request)
    option, _ = build_option(cat_mod.get(catalogue, product_id), request, params)
    return option
