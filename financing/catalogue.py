"""Fixed, dated product catalogue (PRD/07 §2, FIN-01, CTL-FIN-01).

``load()`` validates ``data/products.json``: every product must validate as ``ProductDTO``
(unknown keys fail), every param must be a finite Decimal string, each kind must carry
exactly its own param keys, and product ids must be unique. Asking for a product id that
is not in the catalogue raises ``CatalogueMiss``.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pydantic import ValidationError

from contracts.dto_financing import CatalogueDTO, ProductDTO
from contracts.enums import ProductKind
from contracts.errors import CatalogueMiss, InvalidParams
from infra.paths import DATA_DIR

CATALOGUE_PATH = DATA_DIR / "products.json"

# The exact param keys each product kind needs (PRD/07 §3).
PARAM_KEYS: dict[ProductKind, frozenset[str]] = {
    ProductKind.SUPPLIER_CREDIT: frozenset({"net_days", "discount_rate", "discount_days"}),
    ProductKind.OVERDRAFT: frozenset({"apr", "fee_cents", "limit_cents"}),
    ProductKind.BANK_LOAN: frozenset({"apr", "months", "origination_fee_rate"}),
    ProductKind.INVOICE_FINANCING: frozenset(
        {"advance_rate", "fee_rate_per_30d", "flat_fee_cents"}
    ),
    ProductKind.MICRO_LOAN: frozenset({"flat_fee_rate", "term_days"}),
    ProductKind.MERCHANT_CASH_ADVANCE: frozenset({"factor", "holdback_rate"}),
}

# Params that must be whole numbers (days, months, cents).
_INTEGER_PARAMS = frozenset(
    {
        "net_days",
        "discount_days",
        "fee_cents",
        "limit_cents",
        "months",
        "flat_fee_cents",
        "term_days",
    }
)


def param(product: ProductDTO, key: str) -> Decimal:
    """A product param as ``Decimal`` (already validated at load)."""
    return Decimal(product.params[key])


def _validate_product(product: ProductDTO) -> None:
    expected = PARAM_KEYS[product.kind]
    keys = set(product.params)
    if keys != expected:
        raise InvalidParams(
            f"{product.product_id}: params must be exactly {sorted(expected)}, got {sorted(keys)}",
            details={"product_id": product.product_id},
        )
    for key, raw in product.params.items():
        try:
            value = Decimal(raw)
        except InvalidOperation as exc:
            raise InvalidParams(
                f"{product.product_id}: param {key} is not a decimal string: {raw!r}",
                details={"product_id": product.product_id, "param": key},
            ) from exc
        if not value.is_finite() or value < 0:
            raise InvalidParams(
                f"{product.product_id}: param {key} must be finite and >= 0",
                details={"product_id": product.product_id, "param": key},
            )
        if key in _INTEGER_PARAMS and value != value.to_integral_value():
            raise InvalidParams(
                f"{product.product_id}: param {key} must be a whole number",
                details={"product_id": product.product_id, "param": key},
            )
    if not 0 < product.min_amount_cents <= product.max_amount_cents:
        raise InvalidParams(f"{product.product_id}: bad amount range")
    if not 0 < product.min_term_days <= product.max_term_days:
        raise InvalidParams(f"{product.product_id}: bad term range")


def parse(raw: object) -> CatalogueDTO:
    """Validate a raw catalogue object into a ``CatalogueDTO``."""
    try:
        cat = CatalogueDTO.model_validate(raw)
    except ValidationError as exc:
        raise InvalidParams(f"invalid product catalogue: {exc}") from exc
    seen: set[str] = set()
    for product in cat.products:
        if product.product_id in seen:
            raise InvalidParams(f"duplicate product_id {product.product_id}")
        seen.add(product.product_id)
        _validate_product(product)
    return cat


def load(path: Path | None = None) -> CatalogueDTO:
    """Load and validate ``data/products.json`` (or ``path``)."""
    p = path or CATALOGUE_PATH
    with p.open(encoding="utf-8") as fh:
        return parse(json.load(fh))


def get(catalogue: CatalogueDTO, product_id: str) -> ProductDTO:
    """The product with ``product_id``; raises ``CatalogueMiss`` if it is not listed."""
    for product in catalogue.products:
        if product.product_id == product_id:
            return product
    raise CatalogueMiss(
        f"product {product_id!r} is not in the catalogue", details={"product_id": product_id}
    )
