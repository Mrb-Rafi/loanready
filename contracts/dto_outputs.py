"""Reminder, delay-request and loan-pack DTOs (CON-14)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from contracts._model import Frozen
from contracts.dto_books import (
    BalanceSheetDTO,
    CashFlowStatementDTO,
    ProfitAndLossDTO,
    ReceivablesAgeingDTO,
    ReconciliationDTO,
    WeeklySales,
)
from contracts.dto_financing import FinancingComparisonDTO, ReadinessDTO
from contracts.dto_planning import ForecastDTO
from contracts.enums import Channel, DraftKind, Framework, GeneratedBy, Tone


class ReminderFacts(Frozen):
    invoice_id: str
    invoice_number: str
    customer_name: str
    to_ref: str
    channel: Channel
    amount_outstanding_cents: int
    amount_str: str
    currency: str
    due_date: date
    due_date_str: str  # "14 Oct 2026"
    days_overdue: int
    tone: Tone
    business_name: str


class DelayFacts(Frozen):
    bill_id: str
    bill_number: str
    supplier_name: str
    to_ref: str
    channel: Channel
    amount_cents: int
    amount_str: str
    due_date: date
    proposed_date: date
    due_date_str: str
    proposed_date_str: str
    business_name: str


class MessageDraftDTO(Frozen):
    draft_id: str
    kind: DraftKind
    channel: Channel
    to_ref: str
    subject: str
    body: str
    body_sha256: str
    facts_hash: str
    generated_by: GeneratedBy
    ai_label: str
    related_id: str
    created_at: datetime
    status: Literal["draft", "sent"]
    sbx_ref: str | None


LOAN_PACK_SECTIONS = (
    "cover",
    "summary",
    "pnl",
    "cash_flow",
    "balance_sheet",
    "forecast",
    "reconciliation",
    "ageing",
    "sales_trend",
    "readiness",
    "financing",
    "policies",
    "controls",
)
LENDER_REQUIRED_SECTIONS = (
    "cover",
    "pnl",
    "cash_flow",
    "balance_sheet",
    "forecast",
    "reconciliation",
    "ageing",
    "readiness",
    "policies",
)


class LoanPackContent(Frozen):
    business_name: str
    as_of: date
    framework: Framework
    requested_amount_cents: int
    term_days: int
    purpose: str
    pnl: ProfitAndLossDTO
    cash_flow: CashFlowStatementDTO
    balance_sheet: BalanceSheetDTO
    forecast: ForecastDTO
    reconciliation: ReconciliationDTO
    ageing: ReceivablesAgeingDTO
    weekly_sales: list[WeeklySales]
    readiness: ReadinessDTO
    financing: FinancingComparisonDTO | None
    going_concern_note: str | None
    accounting_policies: list[str]
    audit_final_hash: str | None = None  # controls section (PRD/11 ORC-09)
    audit_chain_ok: bool | None = None


class LoanPackDTO(Frozen):
    pack_id: str
    path: str
    sha256: str
    sections: list[str]
    created_at: datetime
    metadata: dict[str, Any]  # business, as_of, requested amount, framework, section list
