"""Neutral ranking, flags, notices, catalogue miss and the S4 ordering."""

from __future__ import annotations

from datetime import date

import pytest

from contracts.dto_financing import CatalogueDTO
from contracts.enums import ProductKind
from contracts.errors import CatalogueMiss, InvalidParams
from financing import builders
from financing import compare as compare_mod
from financing.compare import (
    ABOVE_CN_PRIVATE_LENDING_CAP,
    CN_CAP_SKIPPED_NOTICE,
    HIGH_COST,
    NOT_A_LENDER_NOTICE,
    NOT_ADVICE_NOTICE,
)


def _s4_request(h):
    """Representative S4 borrow request: the gap left after COLLECT and DELAY, needed until
    the end of the crunch, Golden Mills terms available, card sales and two open invoices."""
    return h.req(
        amount_cents=120000,
        term_days=21,
        avg_daily_card_sales_cents=15000,
        supplier_terms_available=True,
        eligible_invoices=[h.invoice("INV-1", 80000, 25), h.invoice("INV-2", 90000, 14)],
    )


def test_s4_ordering(cat, params, h) -> None:
    """T-FIN-06 proves FIN-04, CTL-FIN-03: cheapest is supplier credit (EAR 0%), most
    expensive is the MCA."""
    result = compare_mod.compare(cat, params, _s4_request(h))
    by_id = {o.product_id: o for o in result.options}
    assert result.cheapest_id == "sup_goldenmills_net30"
    assert by_id[result.cheapest_id].kind is ProductKind.SUPPLIER_CREDIT
    assert by_id[result.cheapest_id].ear == 0
    assert result.most_expensive_id == "mca_swiftadvance"


def test_ranking_by_ear_and_ineligible_unranked(cat, params, h) -> None:
    """T-FIN-06 proves CTL-FIN-03: eligible ranked 1..n by EAR ascending; ineligible None."""
    result = compare_mod.compare(cat, params, _s4_request(h))
    eligible = sorted((o for o in result.options if o.eligible), key=lambda o: o.rank)
    assert [o.rank for o in eligible] == list(range(1, len(eligible) + 1))
    ears = [o.ear for o in eligible]
    assert ears == sorted(ears)
    loan = next(o for o in result.options if o.product_id == "loan_harbour_wc12")
    assert not loan.eligible  # 1,200 is below the 2,000 minimum
    assert loan.rank is None
    assert loan.ineligibility_reasons
    assert all(o.rank is None for o in result.options if not o.eligible)


def test_ties_break_on_cost_then_id(params, h) -> None:
    """T-FIN-06: equal EAR ties break by total cost, then product_id."""
    supplier = {"net_days": "30", "discount_rate": "0", "discount_days": "0"}
    cat = CatalogueDTO(
        as_of=date(2026, 10, 14),
        source="test",
        currency="USD",
        products=[
            h.product(ProductKind.SUPPLIER_CREDIT, supplier, product_id="sup_b"),
            h.product(ProductKind.SUPPLIER_CREDIT, supplier, product_id="sup_a"),
        ],
    )
    result = compare_mod.compare(cat, params, h.req())
    ranks = {o.product_id: o.rank for o in result.options}
    assert ranks == {"sup_a": 1, "sup_b": 2}
    assert result.cheapest_id == "sup_a"
    assert result.most_expensive_id == "sup_b"


def test_no_eligible_options(cat, params, h) -> None:
    """T-FIN-06: nothing eligible -> no cheapest / most expensive."""
    result = compare_mod.compare(
        cat,
        params,
        h.req(
            amount_cents=90_000_000,
            avg_daily_card_sales_cents=0,
            supplier_terms_available=False,
        ),
    )
    assert result.cheapest_id is None
    assert result.most_expensive_id is None
    assert all(o.rank is None for o in result.options)


def test_flags(cat, params, h) -> None:
    """T-FIN-07 proves CTL-FIN-02: HIGH_COST above 36% APR, FACTOR_RATE_PRODUCT on the MCA,
    TERM_* flags; the CN cap flag is never set while the LPR is not applied."""
    result = compare_mod.compare(cat, params, _s4_request(h))
    by_id = {o.product_id: o for o in result.options}
    assert HIGH_COST in by_id["mca_swiftadvance"].flags
    assert builders.FACTOR_RATE_PRODUCT in by_id["mca_swiftadvance"].flags
    assert HIGH_COST in by_id["micro_quickfund30"].flags
    assert HIGH_COST not in by_id["od_harbour"].flags
    assert HIGH_COST not in by_id["sup_goldenmills_net30"].flags
    assert builders.TERM_LONGER_THAN_NEED in by_id["loan_harbour_wc12"].flags
    long_need = compare_mod.compare(cat, params, h.req(amount_cents=120000, term_days=45))
    micro = next(o for o in long_need.options if o.product_id == "micro_quickfund30")
    assert builders.TERM_SHORTER_THAN_NEED in micro.flags
    for o in result.options:
        assert ABOVE_CN_PRIVATE_LENDING_CAP not in o.flags
    assert params.china_private_lending_cap.lpr_1y is None
    assert CN_CAP_SKIPPED_NOTICE in result.notices


def test_notices_always_present(cat, params, h) -> None:
    """T-FIN-06 proves REG-LND-05, REG-LND-06, CTL-FIN-03: dated, not-a-lender, not-advice."""
    result = compare_mod.compare(cat, params, _s4_request(h))
    assert result.notices[:3] == [
        "Illustrative products dated 2026-10-14; not offers.",
        NOT_A_LENDER_NOTICE,
        NOT_ADVICE_NOTICE,
    ]
    assert result.catalogue_as_of == cat.as_of
    assert result.catalogue_source == cat.source
    joined = " ".join(result.notices).lower()
    for banned in ("recommended", "best for you", "sponsored"):
        assert banned not in joined


def test_catalogue_miss(cat, params, h) -> None:
    """T-FIN-09 proves FIN-01, CTL-FIN-01: unknown product ids raise CatalogueMiss."""
    with pytest.raises(CatalogueMiss):
        compare_mod.compare(cat, params, h.req(), product_ids=["loan_not_listed"])
    with pytest.raises(CatalogueMiss):
        compare_mod.quote(cat, params, h.req(), "loan_not_listed")
    subset = compare_mod.compare(cat, params, h.req(), product_ids=["od_harbour"])
    assert [o.product_id for o in subset.options] == ["od_harbour"]


def test_invalid_request(cat, params, h) -> None:
    """T-FIN-05: non-positive amount or term is refused."""
    with pytest.raises(InvalidParams):
        compare_mod.compare(cat, params, h.req(amount_cents=0))
    with pytest.raises(InvalidParams):
        compare_mod.compare(cat, params, h.req(term_days=0))
    with pytest.raises(InvalidParams):
        compare_mod.compare(cat, params, h.req(avg_daily_card_sales_cents=-1))


def test_deterministic(cat, params, h) -> None:
    """T-FIN-06: same request -> identical comparison."""
    a = compare_mod.compare(cat, params, _s4_request(h))
    b = compare_mod.compare(cat, params, _s4_request(h))
    assert a.model_dump() == b.model_dump()
