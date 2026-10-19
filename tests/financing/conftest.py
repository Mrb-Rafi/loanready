"""WP5 test helpers: the real catalogue and params, and request builders."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from contracts.dto_financing import (
    CatalogueDTO,
    EligibleInvoice,
    FinancingRequest,
    ProductDTO,
)
from contracts.enums import ProductKind
from financing import catalogue, regparams
from financing.regparams import RegParams

AS_OF = date(2026, 10, 14)


def make_request(**kw: Any) -> FinancingRequest:
    base: dict[str, Any] = dict(
        amount_cents=100000,
        term_days=30,
        as_of=AS_OF,
        avg_daily_card_sales_cents=8889,
        eligible_invoices=[],
        supplier_terms_available=True,
    )
    base.update(kw)
    return FinancingRequest(**base)


def make_product(kind: ProductKind, params: dict[str, str], **kw: Any) -> ProductDTO:
    base: dict[str, Any] = dict(
        product_id=f"test_{kind.value.lower()}",
        kind=kind,
        name=f"Test {kind.value}",
        provider="Test provider (fictional)",
        params=params,
        min_amount_cents=1,
        max_amount_cents=100_000_000,
        min_term_days=1,
        max_term_days=1000,
    )
    base.update(kw)
    return ProductDTO(**base)


@pytest.fixture(scope="session")
def cat() -> CatalogueDTO:
    return catalogue.load()


@pytest.fixture(scope="session")
def params() -> RegParams:
    return regparams.load()


def invoice(inv_id: str, cents: int, days: int) -> EligibleInvoice:
    return EligibleInvoice(invoice_id=inv_id, outstanding_cents=cents, expected_days_to_pay=days)


@pytest.fixture(scope="session")
def h() -> Any:
    """Helper namespace (``h.req(...)``, ``h.product(...)``, ``h.invoice(...)``); test modules
    cannot import conftest under ``--import-mode=importlib``."""

    class _H:
        req = staticmethod(make_request)
        product = staticmethod(make_product)
        invoice = staticmethod(invoice)

    return _H
