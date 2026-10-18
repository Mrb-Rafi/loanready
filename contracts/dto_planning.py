"""Forecast and cash-plan DTOs (CON-12)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from contracts._model import Frozen
from contracts.dto_books import RecurringObligation, WeeklySales
from contracts.dto_financing import FinancingRequest
from contracts.enums import ActionType, Lever, Scenario


class InvoiceForecastItem(Frozen):
    invoice_id: str
    customer_id: str
    due_date: date
    outstanding_cents: int
    lateness_history_days: list[int]


class BillForecastItem(Frozen):
    bill_id: str
    supplier_id: str
    due_date: date
    outstanding_cents: int
    category: str
    late_penalty: bool
    essential: bool
    grace_days: int


class EventUplift(Frozen):
    name: str
    start: date
    end: date
    factor: Decimal  # 1.30 = +30% sales


class ForecastInputs(Frozen):
    as_of: date
    opening_cash_cents: int
    weekly_sales: list[WeeklySales]
    cogs_ratio: Decimal
    open_invoices: list[InvoiceForecastItem]
    open_bills: list[BillForecastItem]
    recurring: list[RecurringObligation]
    events: list[EventUplift]
    safety_buffer_cents: int | None = None


class WeekRow(Frozen):
    week: int
    start: date
    end: date
    opening_cents: int
    in_sales_cents: int
    in_collections_cents: int
    out_cogs_cents: int
    out_bills_cents: int
    out_rent_cents: int
    out_wages_cents: int
    out_utilities_cents: int
    out_loan_cents: int
    out_other_cents: int
    net_cents: int
    closing_cents: int


class ScenarioForecast(Frozen):
    scenario: Scenario
    weeks: list[WeekRow]
    first_negative_week: int | None
    min_closing_cents: int
    min_closing_week: int
    assumptions: list[str]


class DriverItem(Frozen):
    kind: Literal["outflow", "delayed_inflow"]
    ref_id: str
    label: str
    amount_cents: int
    week: int
    expected_date: date


class DriversDTO(Frozen):
    scenario: Scenario
    crossing_week: int | None
    top_outflows: list[DriverItem]
    delayed_inflows: list[DriverItem]
    facts: dict[str, str]  # for the explanation sentence


class ForecastDTO(Frozen):
    as_of: date
    scenarios: dict[Scenario, ScenarioForecast]
    drivers: DriversDTO
    recommended_gap_cents: int
    base_gap_cents: int
    needed_term_days: int
    going_concern_flag: bool
    inputs_hash: str


class ProposedDraft(Frozen):
    action_type: ActionType
    params: dict[str, Any]
    reason: str


class CashPlanStep(Frozen):
    lever: Lever
    gap_before_cents: int
    gap_after_cents: int
    closed_cents: int
    item_refs: list[str]
    explanation_facts: dict[str, str]


class CashPlanDTO(Frozen):
    as_of: date
    initial_gap_cents: int
    steps: list[CashPlanStep]
    drafts: list[ProposedDraft]
    financing_request: FinancingRequest | None
    remaining_gap_cents: int
