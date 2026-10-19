"""Catalogue and regulatory-params schemas; the service wiring."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from contracts.dto_financing import FinancingComparisonDTO
from contracts.errors import CatalogueMiss, InvalidParams
from financing import Financing, Readiness, catalogue, regparams


def _raw_catalogue() -> dict:
    with catalogue.CATALOGUE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def _raw_params() -> dict:
    with regparams.REGPARAMS_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def test_catalogue_loads_dated_and_sourced(cat) -> None:
    """T-FIN-09 proves FIN-01, CTL-FIN-01: the shipped catalogue validates; as_of and source."""
    assert cat.as_of.isoformat() == "2026-10-14"
    assert "Not real offers" in cat.source
    assert cat.currency == "USD"
    ids = [p.product_id for p in cat.products]
    assert len(ids) == len(set(ids)) == 7
    assert {p.kind for p in cat.products} == set(catalogue.PARAM_KEYS)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw["products"][0].update(sponsored=True),
        lambda raw: raw.update(extra="x"),
        lambda raw: raw["products"][2]["params"].update(apr="abc"),
        lambda raw: raw["products"][2]["params"].update(apr="NaN"),
        lambda raw: raw["products"][2]["params"].update(apr="-0.1"),
        lambda raw: raw["products"][2]["params"].update(bonus="1"),
        lambda raw: raw["products"][2]["params"].pop("apr"),
        lambda raw: raw["products"][3]["params"].update(months="12.5"),
        lambda raw: raw["products"][1].update(product_id=raw["products"][0]["product_id"]),
        lambda raw: raw["products"][0].update(kind="PAYDAY"),
        lambda raw: raw["products"][0].update(min_amount_cents=900000),
        lambda raw: raw["products"][0].update(min_term_days=0),
    ],
)
def test_catalogue_rejects_bad_input(mutate) -> None:
    """T-FIN-09 proves FIN-01: unknown keys or invalid Decimal strings fail at load."""
    raw = copy.deepcopy(_raw_catalogue())
    mutate(raw)
    with pytest.raises(InvalidParams):
        catalogue.parse(raw)


def test_catalogue_get(cat) -> None:
    """T-FIN-09 proves CTL-FIN-01: get() returns listed products and refuses others."""
    assert catalogue.get(cat, "od_harbour").params["apr"] == "0.18"
    with pytest.raises(CatalogueMiss):
        catalogue.get(cat, "unknown")


def test_load_from_path(tmp_path: Path) -> None:
    """T-FIN-09: load() accepts an explicit UTF-8 path."""
    p = tmp_path / "products.json"
    p.write_text(json.dumps(_raw_catalogue()), encoding="utf-8")
    assert catalogue.load(p).as_of.isoformat() == "2026-10-14"


def test_regparams(params) -> None:
    """T-FIN-09: params load; LPR placeholder becomes None (never an invented value)."""
    assert params.high_cost_flag_apr == Decimal("0.36")
    assert params.dscr_comfort == Decimal("1.25")
    assert params.ecl_provision_matrix["90+"] == Decimal("0.50")
    assert params.china_private_lending_cap.lpr_1y is None
    assert params.cn_cap_apr is None


def test_regparams_lpr_set_and_bad_input() -> None:
    """T-FIN-09: a filled LPR parses; unknown keys and bad decimals fail."""
    raw = copy.deepcopy(_raw_params())
    raw["china_private_lending_cap"]["lpr_1y"] = "0.03"
    assert regparams.parse(raw).cn_cap_apr == Decimal("0.12")
    for mutate in (
        lambda r: r.update(extra="x"),
        lambda r: r.update(high_cost_flag_apr="abc"),
        lambda r: r.update(high_cost_flag_apr=0.36),
        lambda r: r.update(china_private_lending_cap="x"),
        lambda r: r.update(ecl_provision_matrix=[]),
        lambda r: r.update(as_of="not a date"),
    ):
        bad = copy.deepcopy(_raw_params())
        mutate(bad)
        with pytest.raises(InvalidParams):
            regparams.parse(bad)
    with pytest.raises(InvalidParams):
        regparams.parse([])  # type: ignore[arg-type]


def test_service_wiring(h) -> None:
    """T-FIN-09: Financing and Readiness satisfy the service protocols' methods."""
    from contracts.testing.builders import make_readiness_inputs

    fin = Financing()
    assert fin.catalogue().as_of.isoformat() == "2026-10-14"
    assert fin.product("od_harbour").name == "Business overdraft"
    result = fin.compare(h.req())
    assert isinstance(result, FinancingComparisonDTO)
    assert fin.quote(h.req(), "od_harbour").product_id == "od_harbour"
    with pytest.raises(CatalogueMiss):
        fin.quote(h.req(), "nope")
    r = Readiness().score(make_readiness_inputs())
    assert 0 <= r.score <= 100
