"""Books DTOs (CON-11). Money is int cents (fields end with ``_cents``); rates are Decimal."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from contracts._model import Frozen
from contracts.enums import (
    AccountType,
    BillStatus,
    CategorisationMethod,
    ConfirmKind,
    CounterpartyKind,
    EntrySource,
    EntryStatus,
    Framework,
    InvoiceStatus,
    OcrBackend,
    PayeeStatus,
    RawStatus,
    ReceiptStatus,
    SourceKind,
)
from contracts.untrusted import ScanFlag, UntrustedText


class AccountDTO(Frozen):
    code: str
    name: str
    type: AccountType
    is_cash: bool


class CounterpartyDTO(Frozen):
    id: str
    name: str
    kind: CounterpartyKind
    payment_terms_days: int
    lateness_history_days: list[int]  # observed days late on past invoices (customers)
    contact_ref: str | None  # email or phone (synthetic)


class PayeeDTO(Frozen):
    id: str
    counterparty_id: str
    name: str
    bank_account_ref: str
    first_paid_on: date | None
    status: PayeeStatus
    preregistered_by_owner: bool


class InvoiceDTO(Frozen):
    id: str
    number: str
    customer_id: str
    issue_date: date
    due_date: date
    amount_cents: int
    paid_cents: int
    status: InvoiceStatus

    @property
    def outstanding_cents(self) -> int:
        """amount - paid (never negative)."""
        return max(0, self.amount_cents - self.paid_cents)

    def days_overdue(self, as_of: date) -> int:
        """(as_of - due_date) in days, floored at 0."""
        return max(0, (as_of - self.due_date).days)


class BillDTO(Frozen):
    id: str
    number: str
    supplier_id: str
    payee_id: str | None
    issue_date: date
    due_date: date
    amount_cents: int
    paid_cents: int
    status: BillStatus
    category: str  # account code
    late_penalty: bool
    grace_days: int
    essential: bool
    claimed_bank_account_ref: UntrustedText | None  # from the document, untrusted
    description: UntrustedText | None

    @property
    def outstanding_cents(self) -> int:
        """amount - paid (never negative)."""
        return max(0, self.amount_cents - self.paid_cents)


class RawTransactionDTO(Frozen):
    id: str
    source: SourceKind
    source_ref: str
    date: date
    amount_cents: int  # signed: + money in, - money out (bank perspective)
    description: UntrustedText
    counterparty_hint: str | None
    tender: str | None  # cash | card for POS
    payload_hash: str
    status: RawStatus


class CategorisationResult(Frozen):
    raw_txn_id: str
    label: str  # account code or "PERSONAL"
    confidence: Decimal
    method: CategorisationMethod
    needs_confirmation: bool
    rule_id: str | None


class ConfirmItemDTO(Frozen):
    id: str
    kind: ConfirmKind
    ref_id: str
    question: str
    proposed: str
    options: list[str]
    confidence: Decimal | None
    created_at: datetime


class JournalLineDTO(Frozen):
    account_code: str
    debit_cents: int
    credit_cents: int


class JournalEntryDTO(Frozen):
    id: str
    date: date
    memo: str
    source: EntrySource
    source_ref: str
    status: EntryStatus
    lines: list[JournalLineDTO]
    created_by: str
    reversal_of: str | None


class StatementLine(Frozen):
    key: str
    label: str
    values_cents: dict[str, int]  # period key -> cents
    level: int  # 0 heading, 1 line, 2 subtotal, 3 total


class ProfitAndLossDTO(Frozen):
    framework: Framework
    periods: list[str]
    lines: list[StatementLine]
    net_profit_cents: dict[str, int]
    total_net_profit_cents: int


class CashFlowStatementDTO(Frozen):
    framework: Framework
    method: Literal["direct"]
    start: date
    end: date
    opening_cash_cents: int
    lines: list[StatementLine]  # grouped operating / investing / financing
    net_operating_cents: int
    net_investing_cents: int
    net_financing_cents: int
    closing_cash_cents: int


class BalanceSheetDTO(Frozen):
    framework: Framework
    as_of: date
    lines: list[StatementLine]
    total_assets_cents: int
    total_liabilities_cents: int
    total_equity_cents: int


class ReconMatch(Frozen):
    bank_txn_id: str
    journal_entry_id: str
    amount_cents: int
    date_diff_days: int
    score: Decimal


class ReconciliationDTO(Frozen):
    as_of: date
    matched: list[ReconMatch]
    bank_only: list[str]
    books_only: list[str]
    duplicates: list[list[str]]
    ledger_cash_cents: int
    bank_balance_cents: int
    difference_cents: int
    reconciling_items: list[StatementLine]
    is_reconciled: bool


class AgeingRow(Frozen):
    invoice_id: str
    customer_name: str
    outstanding_cents: int
    days_overdue: int
    bucket: str


AGEING_BUCKETS = ("current", "1-30", "31-60", "61-90", "90+")


class ReceivablesAgeingDTO(Frozen):
    as_of: date
    buckets_cents: dict[str, int]  # "current","1-30","31-60","61-90","90+"
    rows: list[AgeingRow]
    ecl_rates: dict[str, Decimal]
    ecl_allowance_cents: int


class ReceiptDTO(Frozen):
    id: str
    image_path: str
    ocr_text: UntrustedText
    ocr_backend: OcrBackend
    vendor: str | None
    total_cents: int | None
    date: date | None
    confidence: Decimal
    status: ReceiptStatus
    flags: list[ScanFlag]


class WeeklySales(Frozen):
    week_start: date
    cash_cents: int
    card_sales_cents: int
    total_cents: int


class RecurringObligation(Frozen):
    """monthly: on day_of_month each month from first_date (clamped to month length);
    every_n_days: first_date + n * every_days."""

    name: str
    category: str
    amount_cents: int
    first_date: date
    schedule: Literal["monthly", "every_n_days"]
    day_of_month: int | None
    every_days: int | None
    end_date: date | None
    essential: bool
