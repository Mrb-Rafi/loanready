"""HTTP models shared by the sandbox server (WP2) and the adapters (CON-19).

Full behaviour: PRD/04_wp2_sandbox.md. Headers: ``Authorization: Bearer <token>``,
``Idempotency-Key: <action_id>``, ``X-Ticket-Id: <ticket_id>`` (informational); response
headers ``Idempotent-Replay``, ``X-Next-Cursor``, ``Retry-After``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from contracts._model import Frozen
from contracts.untrusted import UntrustedText

HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"
HEADER_TICKET_ID = "X-Ticket-Id"
HEADER_IDEMPOTENT_REPLAY = "Idempotent-Replay"
HEADER_NEXT_CURSOR = "X-Next-Cursor"
HEADER_RETRY_AFTER = "Retry-After"
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

PaymentStatus = Literal["scheduled", "settled", "cancelled", "failed", "returned"]
TxnKind = Literal["opening", "seed", "payment", "payment_return", "admin"]


class BankAccountOut(Frozen):
    id: str
    name: str
    currency: str
    status: Literal["open", "frozen"]
    balance_cents: int


class BalanceOut(Frozen):
    account_id: str
    balance_cents: int
    available_cents: int
    today: date


class BankTxnOut(Frozen):
    id: str
    account_id: str
    date: date
    amount_cents: int  # signed: + in, - out
    description: UntrustedText
    counterparty: str | None
    kind: TxnKind
    payment_ref: str | None
    balance_after_cents: int


class StatementOut(Frozen):
    account_id: str
    month: str  # YYYY-MM
    opening_cents: int
    closing_cents: int
    transactions: list[BankTxnOut]


class PayeeOut(Frozen):
    id: str
    name: str
    bank_account_ref: str
    first_paid_on: date | None
    status: Literal["known", "closed"]


class AdminPayeeIn(Frozen):
    id: str
    name: str
    bank_account_ref: str


class PaymentScheduleIn(Frozen):
    from_account_id: str
    payee_id: str
    bank_account_ref: str
    amount_cents: int
    value_date: date
    reference: str = Field(max_length=60)


class PaymentOut(Frozen):
    sbx_ref: str
    from_account_id: str
    payee_id: str
    amount_cents: int
    value_date: date
    reference: str
    status: PaymentStatus
    failure_code: str | None
    settled_on: date | None
    created_at: datetime


class ClockIn(Frozen):
    today: date


class ClockOut(Frozen):
    today: date


class PosItem(Frozen):
    name: str
    qty: int
    unit_cents: int


class PosSaleOut(Frozen):
    id: str
    ts: datetime
    amount_cents: int  # = sum(qty * unit_cents)
    tender: Literal["cash", "card"]
    items: list[PosItem]
    note: UntrustedText | None = None


class MessageSendIn(Frozen):
    channel: Literal["EMAIL", "SMS"]
    to_ref: str
    subject: str
    body: str


class MessageOut(Frozen):
    sbx_ref: str
    channel: Literal["EMAIL", "SMS"]
    to_ref: str
    subject: str
    body_sha256: str
    status: str  # "delivered_sandbox"
    created_at: datetime


class LenderApplicationIn(Frozen):
    lender_id: str
    business_name: str
    requested_amount_cents: int
    term_days: int
    purpose: str
    pack_sha256: str
    sections: list[str]
    pdf_base64: str


class LenderReceiptOut(Frozen):
    receipt_id: str
    status: Literal["received_under_review"]
    pack_sha256: str
    received_at: datetime
    missing_sections: list[str]


class ErrorOut(Frozen):
    code: str
    message: str
    details: dict[str, Any] | None = None


# Error catalogue codes (PRD/04 §7). Both sides share this list.
SANDBOX_ERROR_CODES: tuple[str, ...] = (
    "UNAUTHENTICATED",
    "INSUFFICIENT_SCOPE",
    "IDEMPOTENCY_KEY_REQUIRED",
    "IDEMPOTENCY_CONFLICT",
    "RATE_LIMITED",
    "VALIDATION_ERROR",
    "UNKNOWN_ACCOUNT",
    "ACCOUNT_FROZEN",
    "UNKNOWN_PAYMENT",
    "PAYEE_UNKNOWN",
    "PAYEE_CLOSED",
    "ACCOUNT_MISMATCH",
    "INVALID_AMOUNT",
    "INVALID_VALUE_DATE",
    "VALUE_DATE_TOO_FAR",
    "BANK_LIMIT_EXCEEDED",
    "INSUFFICIENT_FUNDS",
    "NOT_CANCELLABLE",
    "INVALID_CURSOR",
    "CLOCK_BACKWARDS",
    "INVALID_RECIPIENT",
    "MESSAGE_TOO_LONG",
    "PACK_HASH_MISMATCH",
    "UNKNOWN_LENDER",
    "UNKNOWN_APPLICATION",
    "PDF_INVALID",
)
# Failure codes on PaymentOut.status == "failed" (not HTTP errors).
PAYMENT_FAILURE_CODES: tuple[str, ...] = (
    "PAYEE_ACCOUNT_CLOSED",
    "INSUFFICIENT_FUNDS_AT_SETTLEMENT",
)
