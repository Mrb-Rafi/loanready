"""Lender-readiness score: component maths, bands, improvements, monotonicity, no personal data."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.dto_financing import ReadinessInputs
from contracts.enums import ReadinessBand
from contracts.testing.builders import make_readiness_inputs
from financing import readiness
from financing.readiness import DISCLAIMER, band_for, sales_cv


def _points(dto) -> dict[str, Decimal]:
    return {c.key: c.points for c in dto.components}


PERFECT = dict(
    months_of_records=12,
    categorised_share=Decimal(1),
    reconciled_share=Decimal(1),
    personal_separated_share=Decimal(1),
    unconfirmed_count=0,
    weekly_sales=[100000] * 12,
    weekly_operating_cf=[10000] * 12,
    receivables_cents=50000,
    overdue_receivables_cents=0,
    monthly_sales_cents=400000,
    monthly_debt_service_cents=0,
    monthly_operating_cf_cents=40000,
)


def test_component_maths_builder_inputs() -> None:
    """T-FIN-10 proves CTL-AI-08: each component of make_readiness_inputs() by hand.

    history 6 months -> 15; bookkeeping 10x0.95 + 10x0.90 = 18.5; separation 15 - 2 = 13;
    stability CV 0 -> 15; cash 10/12 x 15 = 12.5; receivables 5x0 + 5x(1 - 3150/10400)
    = 3.49; debt none -> 10. Sum 87.49 -> 87, GOOD.
    """
    dto = readiness.score(make_readiness_inputs())
    assert _points(dto) == {
        "history": Decimal("15.00"),
        "bookkeeping": Decimal("18.50"),
        "separation": Decimal("13.00"),
        "stability": Decimal("15.00"),
        "cash_health": Decimal("12.50"),
        "receivables": Decimal("3.49"),
        "debt_service": Decimal("10.00"),
    }
    assert dto.score == 87
    assert dto.band is ReadinessBand.GOOD
    assert dto.dscr is None
    assert dto.disclaimer == DISCLAIMER
    assert sum(c.weight for c in dto.components) == 100


def test_perfect_and_empty() -> None:
    """T-FIN-10: perfect inputs -> 100; empty business -> LOW with clamped points."""
    assert readiness.score(make_readiness_inputs(**PERFECT)).score == 100
    empty = readiness.score(
        make_readiness_inputs(
            months_of_records=0,
            categorised_share=Decimal(0),
            reconciled_share=Decimal(0),
            personal_separated_share=Decimal(0),
            unconfirmed_count=30,
            weekly_sales=[],
            weekly_operating_cf=[],
            receivables_cents=0,
            overdue_receivables_cents=0,
            monthly_sales_cents=0,
            monthly_debt_service_cents=50000,
            monthly_operating_cf_cents=-1000,
        )
    )
    pts = _points(empty)
    assert pts["separation"] == 0
    assert pts["stability"] == 0
    assert pts["cash_health"] == 0
    assert pts["debt_service"] == 0
    assert pts["receivables"] == 10  # nothing outstanding, nothing overdue
    assert empty.score == 10
    assert empty.band is ReadinessBand.LOW


def test_stability_and_dscr_linear() -> None:
    """T-FIN-10: CV 0.35 -> 7.5 points; DSCR 1.5 -> 5 points; DSCR >= 2 -> 10."""
    sales = [65000, 135000] * 6  # mean 100,000, population stdev 35,000 -> CV 0.35
    assert sales_cv(sales) == Decimal("0.35")
    dto = readiness.score(
        make_readiness_inputs(
            weekly_sales=sales,
            monthly_debt_service_cents=100000,
            monthly_operating_cf_cents=150000,
        )
    )
    assert _points(dto)["stability"] == Decimal("7.50")
    assert _points(dto)["debt_service"] == Decimal("5.00")
    assert dto.dscr == Decimal("1.5000")
    strong = make_readiness_inputs(
        monthly_debt_service_cents=100000, monthly_operating_cf_cents=300000
    )
    assert _points(readiness.score(strong))["debt_service"] == 10


def test_bands() -> None:
    """T-FIN-10: LOW < 50, FAIR 50-74, GOOD >= 75."""
    assert band_for(49) is ReadinessBand.LOW
    assert band_for(50) is ReadinessBand.FAIR
    assert band_for(74) is ReadinessBand.FAIR
    assert band_for(75) is ReadinessBand.GOOD


def test_improvements_recomputed_and_ranked() -> None:
    """T-FIN-10 proves CTL-AI-08: every gain equals re-scoring the improved input; ranked."""
    inputs = make_readiness_inputs(
        months_of_records=4,
        weekly_sales=[65000, 135000] * 6,
        monthly_debt_service_cents=100000,
        monthly_operating_cf_cents=150000,
    )
    dto = readiness.score(inputs)
    gains = [imp.points_gain for imp in dto.improvements]
    assert gains == sorted(gains, reverse=True)
    assert all(g > 0 for g in gains)
    base = readiness.total_points(inputs)
    texts = {imp.text.split(":")[0]: imp for imp in dto.improvements}
    assert texts["Keep records for 2 more months"].points_gain == (
        readiness.total_points(inputs.model_copy(update={"months_of_records": 6})) - base
    )
    assert texts["Keep records for 2 more months"].points_gain == Decimal("5.00")
    assert texts["Confirm 2 pending items"].points_gain == Decimal("2.00")
    assert texts["Confirm 2 pending items"].hint_action == "CATEGORISE"
    collect = texts["Collect $3,150.00 of overdue invoices"]
    assert collect.hint_action == "DRAFT_REMINDER"
    assert collect.points_gain == Decimal("6.51")
    assert collect.text == "Collect $3,150.00 of overdue invoices: +6.5"
    assert any(t.startswith("Steadier weekly sales") for t in texts)
    assert any(t.startswith("Raise monthly operating cash flow") for t in texts)
    assert any(t.startswith("Avoid negative operating cash weeks") for t in texts)


def test_no_improvements_when_perfect() -> None:
    """T-FIN-10: a perfect score has nothing to improve."""
    assert readiness.score(make_readiness_inputs(**PERFECT)).improvements == []


_share = st.decimals(min_value=0, max_value=1, places=2)


@settings(max_examples=60, deadline=None)
@given(
    months=st.integers(0, 12),
    cat=_share,
    rec=_share,
    sep=_share,
    unconfirmed=st.integers(0, 20),
    spread=st.integers(0, 90000),
    overdue=st.integers(0, 400000),
    dscr_cf=st.integers(-100000, 400000),
    which=st.sampled_from(
        ["months", "cat", "rec", "sep", "unconfirmed", "spread", "overdue", "cf", "negweek"]
    ),
)
def test_monotonic(
    months: int,
    cat: Decimal,
    rec: Decimal,
    sep: Decimal,
    unconfirmed: int,
    spread: int,
    overdue: int,
    dscr_cf: int,
    which: str,
) -> None:
    """T-FIN-10 proves FIN-07: improving any one input never lowers the score."""

    def mk(**kw) -> ReadinessInputs:
        s = kw.pop("spread", spread)
        base = dict(
            months_of_records=months,
            categorised_share=cat,
            reconciled_share=rec,
            personal_separated_share=sep,
            unconfirmed_count=unconfirmed,
            weekly_sales=[100000 - s, 100000 + s] * 6,
            weekly_operating_cf=[10000] * 9 + [-5000] * 3,
            receivables_cents=400000,
            overdue_receivables_cents=overdue,
            monthly_sales_cents=300000,
            monthly_debt_service_cents=100000,
            monthly_operating_cf_cents=dscr_cf,
        )
        base.update(kw)
        return make_readiness_inputs(**base)

    before = readiness.score(mk())
    better = {
        "months": dict(months_of_records=months + 1),
        "cat": dict(categorised_share=min(Decimal(1), cat + Decimal("0.1"))),
        "rec": dict(reconciled_share=min(Decimal(1), rec + Decimal("0.1"))),
        "sep": dict(personal_separated_share=min(Decimal(1), sep + Decimal("0.1"))),
        "unconfirmed": dict(unconfirmed_count=max(0, unconfirmed - 1)),
        "spread": dict(spread=max(0, spread - 5000)),
        "overdue": dict(overdue_receivables_cents=max(0, overdue - 10000)),
        "cf": dict(monthly_operating_cf_cents=dscr_cf + 10000),
        "negweek": dict(weekly_operating_cf=[10000] * 10 + [-5000] * 2),
    }[which]
    after = readiness.score(mk(**better))
    assert after.score >= before.score
    assert readiness.total_points(mk(**better)) >= readiness.total_points(mk())


def test_no_personal_attribute_fields() -> None:
    """T-FIN-11 proves FIN-05, REG-AI-01, CTL-AI-08: ReadinessInputs holds business data only."""
    fields = set(ReadinessInputs.model_fields)
    banned = {
        "age",
        "gender",
        "sex",
        "nationality",
        "marital",
        "religion",
        "race",
        "ethnicity",
        "birth",
        "dob",
        "owner",
        "name",
        "disability",
        "citizen",
    }
    for f in fields:
        for b in banned:
            assert b not in f.lower(), f
    assert fields == {
        "as_of",
        "months_of_records",
        "categorised_share",
        "reconciled_share",
        "personal_separated_share",
        "unconfirmed_count",
        "weekly_sales",
        "weekly_operating_cf",
        "receivables_cents",
        "overdue_receivables_cents",
        "monthly_sales_cents",
        "monthly_debt_service_cents",
        "monthly_operating_cf_cents",
    }
