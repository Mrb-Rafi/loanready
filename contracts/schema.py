"""SQLModel tables for ``var/app.db`` (CON-17).

Money is int cents. Every table has ``created_at`` (UTC). Table ownership = the only WP
whose code may INSERT/UPDATE it (ARC-27); others read through service protocols.
Tables live in their own metadata (``APP_METADATA``) so sandbox tables never mix in.
"""

# No ``from __future__ import annotations``: SQLModel evaluates annotations at class
# creation, and a column named ``date`` with a default would shadow the type otherwise.
from datetime import UTC, datetime
from datetime import date as Date
from typing import Any

from sqlalchemy import CheckConstraint, Column, MetaData, String, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import registry
from sqlmodel import Field, SQLModel

from contracts.clock import get_clock

APP_METADATA = MetaData()
_app_registry = registry(metadata=APP_METADATA)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores tz-aware datetimes as ISO-8601 UTC strings ending in Z; returns aware UTC."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime cannot be stored; use UTC-aware datetimes")
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def process_result_value(self, value: str | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _now() -> datetime:
    return get_clock().now()


def _ts(nullable: bool = True) -> Any:
    return Field(default=None, sa_column=Column(UTCDateTime(), nullable=nullable))


def _created() -> Any:
    return Field(default_factory=_now, sa_column=Column(UTCDateTime(), nullable=False))


class AppTable(SQLModel, registry=_app_registry):
    """Base for every app.db table."""


# --- WP3 books --------------------------------------------------------------------------


class Account(AppTable, table=True):
    __tablename__ = "accounts"
    code: str = Field(primary_key=True)
    name: str
    type: str  # AccountType
    is_cash: bool = False
    framework_label_ifrs: str | None = None
    framework_label_gaap: str | None = None
    created_at: datetime = _created()


class Period(AppTable, table=True):
    __tablename__ = "periods"
    month: str = Field(primary_key=True)  # "YYYY-MM"
    status: str = "open"  # "open" | "closed"
    closed_by: str | None = None
    closed_at: datetime | None = _ts()
    created_at: datetime = _created()


class JournalEntry(AppTable, table=True):
    __tablename__ = "journal_entries"
    id: str = Field(primary_key=True)
    date: Date
    memo: str
    source: str  # EntrySource
    source_ref: str
    status: str  # EntryStatus
    created_by: str
    reversal_of: str | None = Field(default=None, foreign_key="journal_entries.id")
    payload_hash: str
    created_at: datetime = _created()


class JournalLine(AppTable, table=True):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit_cents >= 0", name="ck_debit_nonneg"),
        CheckConstraint("credit_cents >= 0", name="ck_credit_nonneg"),
    )
    id: int | None = Field(default=None, primary_key=True)
    entry_id: str = Field(foreign_key="journal_entries.id", index=True)
    account_code: str = Field(foreign_key="accounts.code", index=True)
    debit_cents: int = 0
    credit_cents: int = 0
    created_at: datetime = _created()


class RawTransaction(AppTable, table=True):
    __tablename__ = "raw_transactions"
    __table_args__ = (UniqueConstraint("source", "source_ref", name="uq_raw_source_ref"),)
    id: str = Field(primary_key=True)
    source: str  # SourceKind
    source_ref: str
    date: Date
    amount_cents: int
    description: str
    counterparty_hint: str | None = None
    tender: str | None = None
    payload_hash: str
    status: str  # RawStatus
    category_label: str | None = None
    category_method: str | None = None
    category_confidence: str | None = None  # Decimal as str
    journal_entry_id: str | None = None
    created_at: datetime = _created()


class Counterparty(AppTable, table=True):
    __tablename__ = "counterparties"
    id: str = Field(primary_key=True)
    name: str
    kind: str  # CounterpartyKind
    payment_terms_days: int = 0
    lateness_history_json: str = "[]"
    contact_ref: str | None = None
    created_at: datetime = _created()


class Payee(AppTable, table=True):
    __tablename__ = "payees"
    id: str = Field(primary_key=True)
    counterparty_id: str = Field(foreign_key="counterparties.id")
    name: str
    bank_account_ref: str
    first_paid_on: Date | None = None
    status: str  # PayeeStatus
    preregistered_by_owner: bool = False
    created_at: datetime = _created()


class Invoice(AppTable, table=True):
    __tablename__ = "invoices"
    id: str = Field(primary_key=True)
    number: str = Field(unique=True)
    customer_id: str = Field(foreign_key="counterparties.id")
    issue_date: Date
    due_date: Date
    amount_cents: int
    paid_cents: int = 0
    status: str  # InvoiceStatus
    created_at: datetime = _created()


class Bill(AppTable, table=True):
    __tablename__ = "bills"
    id: str = Field(primary_key=True)
    number: str
    supplier_id: str = Field(foreign_key="counterparties.id")
    payee_id: str | None = Field(default=None, foreign_key="payees.id")
    issue_date: Date
    due_date: Date
    amount_cents: int
    paid_cents: int = 0
    status: str  # BillStatus
    category: str
    late_penalty: bool = False
    grace_days: int = 0
    essential: bool = False
    claimed_bank_account_ref: str | None = None
    description: str | None = None
    created_at: datetime = _created()


class RecurringObligationRow(AppTable, table=True):
    __tablename__ = "recurring_obligations"
    id: str = Field(primary_key=True)
    name: str
    category: str  # account code
    amount_cents: int
    first_date: Date
    schedule: str  # "monthly" | "every_n_days"
    day_of_month: int | None = None
    every_days: int | None = None
    end_date: Date | None = None
    essential: bool = False
    created_at: datetime = _created()


class Receipt(AppTable, table=True):
    __tablename__ = "receipts"
    id: str = Field(primary_key=True)
    image_path: str
    ocr_text: str
    ocr_backend: str  # OcrBackend
    vendor: str | None = None
    total_cents: int | None = None
    date: Date | None = None
    confidence: str  # Decimal as str
    status: str  # ReceiptStatus
    flags_json: str = "[]"
    created_at: datetime = _created()


class ConfirmItem(AppTable, table=True):
    __tablename__ = "confirm_items"
    id: str = Field(primary_key=True)
    kind: str  # ConfirmKind
    ref_id: str
    question: str
    proposed: str
    options_json: str = "[]"
    confidence: str | None = None
    status: str = "open"  # "open" | "resolved"
    resolution: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = _ts()
    created_at: datetime = _created()


# --- WP1 governance -----------------------------------------------------------------------


class ProposalRow(AppTable, table=True):
    __tablename__ = "proposals"
    id: str = Field(primary_key=True)
    action_id: str = Field(unique=True)
    action_json: str
    tier: int
    status: str  # ProposalStatus
    params_hash: str
    summary_json: str
    created_at: datetime = _created()
    decided_at: datetime | None = _ts()


class ApprovalRow(AppTable, table=True):
    __tablename__ = "approvals"
    token_hash: str = Field(primary_key=True)  # sha256 of token_id
    proposal_id: str = Field(foreign_key="proposals.id")
    params_hash: str
    approved_by: str
    approved_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    expires_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    used_at: datetime | None = _ts()
    created_at: datetime = _created()


class TicketRow(AppTable, table=True):
    __tablename__ = "tickets"
    ticket_id: str = Field(primary_key=True)
    action_id: str
    issued_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    expires_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    used_at: datetime | None = _ts()
    created_at: datetime = _created()


class ActionLogRow(AppTable, table=True):
    """Append only (GOV-12): no code path updates or deletes rows."""

    __tablename__ = "action_log"
    seq: int = Field(primary_key=True)  # strictly +1
    ts: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    action_id: str | None = Field(default=None, index=True)
    action_type: str
    tier: int | None = None
    origin: str | None = None
    principal_id: str
    decision: str  # Decision
    reason_code: str  # ReasonCode
    approved_by: str | None = None
    approved_at: datetime | None = _ts()
    params_masked_json: str = "{}"
    result_masked_json: str = "{}"
    sbx_ref: str | None = None
    control_ids_json: str = "[]"
    prev_hash: str
    row_hash: str
    created_at: datetime = _created()


class SettingRow(AppTable, table=True):
    __tablename__ = "settings"
    key: str = Field(primary_key=True)
    value: str
    updated_by: str
    updated_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    created_at: datetime = _created()


# --- WP6 outputs -----------------------------------------------------------------------------


class OutboxDraft(AppTable, table=True):
    __tablename__ = "outbox_drafts"
    id: str = Field(primary_key=True)
    kind: str  # DraftKind
    channel: str  # Channel
    to_ref: str
    subject: str
    body: str
    body_sha256: str
    facts_hash: str
    generated_by: str  # GeneratedBy
    ai_label: str
    related_id: str
    status: str = "draft"  # "draft" | "sent"
    sbx_ref: str | None = None
    created_at: datetime = _created()


class LoanPackRow(AppTable, table=True):
    __tablename__ = "loan_packs"
    id: str = Field(primary_key=True)
    path: str
    sha256: str
    sections_json: str
    metadata_json: str
    created_at: datetime = _created()


class LenderSubmission(AppTable, table=True):
    __tablename__ = "lender_submissions"
    id: str = Field(primary_key=True)
    pack_id: str = Field(foreign_key="loan_packs.id")
    pack_sha256: str
    lender_id: str
    receipt_id: str
    submitted_at: datetime = Field(sa_column=Column(UTCDateTime(), nullable=False))
    created_at: datetime = _created()


TABLE_OWNERS: dict[str, str] = {
    "accounts": "WP3",
    "periods": "WP3",
    "journal_entries": "WP3",
    "journal_lines": "WP3",
    "raw_transactions": "WP3",
    "counterparties": "WP3",
    "payees": "WP3",
    "invoices": "WP3",
    "bills": "WP3",
    "recurring_obligations": "WP3",
    "receipts": "WP3",
    "confirm_items": "WP3",
    "proposals": "WP1",
    "approvals": "WP1",
    "tickets": "WP1",
    "action_log": "WP1",
    "settings": "WP1",
    "outbox_drafts": "WP6",
    "loan_packs": "WP6",
    "lender_submissions": "WP6",
}
