"""Financing and readiness DTOs (CON-13).

Cash-flow sign convention (borrower perspective): ``+`` borrower receives, ``-`` pays.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from contracts._model import Frozen
from contracts.enums import ProductKind, ReadinessBand


class EligibleInvoice(Frozen):
    invoice_id: str
    outstanding_cents: int
    expected_days_to_pay: int


class FinancingRequest(Frozen):
    amount_cents: int
    term_days: int
    as_of: date
    avg_daily_card_sales_cents: int
    eligible_invoices: list[EligibleInvoice]
    supplier_terms_available: bool


class ProductDTO(Frozen):
    product_id: str
    kind: ProductKind
    name: str
    provider: str  # fictional
    params: dict[str, str]  # Decimal strings, e.g. {"apr": "0.18", "fee_cents": "0"}
    min_amount_cents: int
    max_amount_cents: int
    min_term_days: int
    max_term_days: int


class CatalogueDTO(Frozen):
    as_of: date
    source: str
    currency: str
    products: list[ProductDTO]


class CashFlowPoint(Frozen):
    day: int
    amount_cents: int  # + borrower receives, - borrower pays


class DisclosureDTO(Frozen):
    funding_provided_cents: int
    finance_charge_cents: int
    total_repayment_cents: int
    estimated_apr: Decimal | None
    ear: Decimal | None
    payment_amount_cents: int
    payment_frequency: str
    number_of_payments: int
    term_days: int
    prepayment_note: str
    collateral_note: str
    regimes: list[str]  # REG ids satisfied


class FinancingOption(Frozen):
    product_id: str
    kind: ProductKind
    name: str
    provider: str
    eligible: bool
    ineligibility_reasons: list[str]
    flows: list[CashFlowPoint]
    amount_received_cents: int
    total_repaid_cents: int
    total_cost_cents: int
    daily_irr: Decimal | None
    nominal_apr: Decimal | None
    ear: Decimal | None
    simple_annualised: Decimal | None
    term_days: int
    disclosure: DisclosureDTO
    flags: list[str]
    rank: int | None


class FinancingComparisonDTO(Frozen):
    request: FinancingRequest
    options: list[FinancingOption]
    cheapest_id: str | None
    most_expensive_id: str | None
    catalogue_as_of: date
    catalogue_source: str
    notices: list[str]


class ReadinessInputs(Frozen):
    """Business data only: no personal attributes (REG-AI-01, FIN-05)."""

    as_of: date
    months_of_records: int
    categorised_share: Decimal
    reconciled_share: Decimal
    personal_separated_share: Decimal
    unconfirmed_count: int
    weekly_sales: list[int]
    weekly_operating_cf: list[int]
    receivables_cents: int
    overdue_receivables_cents: int
    monthly_sales_cents: int
    monthly_debt_service_cents: int
    monthly_operating_cf_cents: int


class ReadinessComponent(Frozen):
    key: str
    label: str
    weight: int
    points: Decimal
    metric: str
    rule: str


class Improvement(Frozen):
    text: str
    points_gain: Decimal
    hint_action: str | None


class ReadinessDTO(Frozen):
    score: int
    band: ReadinessBand
    components: list[ReadinessComponent]
    improvements: list[Improvement]
    dscr: Decimal | None
    disclaimer: str
