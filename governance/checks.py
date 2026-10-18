"""Entity checks per action type (PRD/03 §3.3). Pure functions over an EntityLookup.

GOV-05: entities are read only through the injected lookup; the bank account compared
against is always the stored payee record, never a value from a document or the model.
Each check returns the CTL ids it exercised, or raises a domain error with a reason code.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from contracts.canonical import file_sha256, sha256_hex
from contracts.dto_books import InvoiceDTO
from contracts.enums import (
    ActionType,
    BillStatus,
    DraftKind,
    InvoiceStatus,
    PayeeStatus,
    ReasonCode,
)
from contracts.errors import EntityCheckFailed, InvalidParams, PeriodLocked
from contracts.registry import LENDER_IDS
from contracts.services import EntityLookup
from infra.paths import REPO_ROOT

OPEN_BILL = (BillStatus.OPEN, BillStatus.PARTIAL)
OPEN_INVOICE = (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)
# (sbx_ref, value_date) of an executed payment by its original action id (CANCEL_PAYMENT).
ExecutedPaymentLookup = Callable[[str], tuple[str | None, date | None] | None]


def _fail(code: ReasonCode, message: str) -> EntityCheckFailed:
    return EntityCheckFailed(message, reason_code=code)


def check_payment(p: Any, lookup: EntityLookup) -> list[str]:
    """SCHEDULE_SUPPLIER_PAYMENT, in the PRD order (CTL-PAY-01, 02, 03, CTL-FR-03)."""
    bill = lookup.bill(p.bill_id)
    if bill is None or bill.status not in OPEN_BILL:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "bill not found or not open")
    payee = lookup.payee(p.payee_id)
    if payee is None:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "payee not found")
    if payee.status == PayeeStatus.BLOCKED:
        raise _fail(ReasonCode.PAYEE_BLOCKED, "payee is blocked")
    known = payee.status == PayeeStatus.KNOWN and (
        payee.first_paid_on is not None or payee.preregistered_by_owner
    )
    if not known:
        raise _fail(ReasonCode.NEW_PAYEE, "payments go to known payees only")
    if p.bank_account_ref != payee.bank_account_ref:
        raise _fail(ReasonCode.ACCOUNT_CHANGED, "account differs from the stored payee account")
    if bill.claimed_bank_account_ref and bill.claimed_bank_account_ref != payee.bank_account_ref:
        raise _fail(
            ReasonCode.ACCOUNT_CHANGED,
            "the bill asks for payment to a different bank account than the one on file",
        )
    if bill.payee_id != p.payee_id:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "payee does not belong to this bill")
    if p.amount_cents > bill.outstanding_cents:
        raise _fail(ReasonCode.AMOUNT_EXCEEDS_OUTSTANDING, "amount exceeds the bill outstanding")
    if lookup.period_is_closed(p.value_date):
        raise PeriodLocked("value date is in a closed period")
    return ["CTL-PAY-01", "CTL-PAY-02", "CTL-PAY-03", "CTL-FR-03"]


def _check_draft(p: Any, lookup: EntityLookup, kind: DraftKind, related_id: str) -> None:
    draft = lookup.draft(p.draft_id)
    if (
        draft is None
        or draft.kind != kind
        or draft.related_id != related_id
        or draft.status != "draft"
    ):
        raise _fail(ReasonCode.DRAFT_MISMATCH, "draft not found, already sent or for another item")
    if sha256_hex(draft.body) != p.body_sha256 or draft.body_sha256 != p.body_sha256:
        raise _fail(ReasonCode.DRAFT_MISMATCH, "message body differs from the approved draft")


def _overdue_invoice(invoice_id: str, lookup: EntityLookup, today: date) -> InvoiceDTO:
    inv = lookup.invoice(invoice_id)
    if inv is None or inv.status not in OPEN_INVOICE:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "invoice not found or not open")
    if inv.days_overdue(today) < 1:
        raise _fail(ReasonCode.NOT_OVERDUE, "invoice is not overdue")
    return inv


def check_send_reminder(p: Any, lookup: EntityLookup, today: date) -> list[str]:
    inv = _overdue_invoice(p.invoice_id, lookup, today)
    _check_draft(p, lookup, DraftKind.REMINDER, inv.id)
    if p.amount_cents != inv.outstanding_cents:
        raise _fail(ReasonCode.AMOUNT_MISMATCH, "amount differs from the invoice outstanding")
    customer = lookup.counterparty(inv.customer_id)
    if customer is None or not customer.contact_ref or p.to_ref != customer.contact_ref:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "recipient is not the customer's stored contact")
    return ["CTL-AI-01", "CTL-AI-06"]


def _delay_eligible_bill(
    bill_id: str, proposed: date, lookup: EntityLookup, open_only: bool
) -> Any:
    bill = lookup.bill(bill_id)
    allowed = (BillStatus.OPEN,) if open_only else OPEN_BILL
    if bill is None or bill.status not in allowed:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "bill not found or not open")
    if bill.late_penalty or bill.essential:
        raise InvalidParams("only penalty-free, non-essential bills can be delayed")
    if (proposed - bill.due_date).days > bill.grace_days:
        raise InvalidParams("proposed date is beyond the grace period")
    return bill


def check_send_delay(p: Any, lookup: EntityLookup) -> list[str]:
    bill = _delay_eligible_bill(p.bill_id, p.proposed_date, lookup, open_only=True)
    _check_draft(p, lookup, DraftKind.DELAY_REQUEST, bill.id)
    supplier = lookup.counterparty(bill.supplier_id)
    if supplier is None or not supplier.contact_ref or p.to_ref != supplier.contact_ref:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "recipient is not the supplier's stored contact")
    return ["CTL-AI-01", "CTL-AI-06"]


def check_submit_pack(p: Any, lookup: EntityLookup) -> list[str]:
    pack = lookup.loan_pack(p.pack_id)
    if pack is None:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "loan pack not found")
    path = Path(pack.path)
    path = path if path.is_absolute() else REPO_ROOT / path
    if pack.sha256 != p.pack_sha256 or not path.exists() or file_sha256(path) != p.pack_sha256:
        raise _fail(ReasonCode.PACK_HASH_MISMATCH, "loan pack file does not match its hash")
    if p.lender_id not in LENDER_IDS:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "unknown lender")
    return ["CTL-FIN-04"]


def check_cancel(p: Any, executed: ExecutedPaymentLookup, today: date) -> list[str]:
    found = executed(p.original_action_id)
    if found is None or found[0] != p.sbx_ref:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "no executed payment with this reference")
    value_date = found[1]
    if value_date is None or value_date <= today:
        raise _fail(ReasonCode.UNKNOWN_ENTITY, "the undo window has closed (value date reached)")
    return ["CTL-PAY-05"]


def check_entities(
    action_type: ActionType,
    params: Any,
    lookup: EntityLookup,
    today: date,
    executed: ExecutedPaymentLookup | None = None,
) -> list[str]:
    """Dispatch to the check for this type; Tier 0 types need none beyond the schema."""
    if action_type == ActionType.SCHEDULE_SUPPLIER_PAYMENT:
        return check_payment(params, lookup)
    if action_type == ActionType.SEND_REMINDER:
        return check_send_reminder(params, lookup, today)
    if action_type == ActionType.SEND_DELAY_REQUEST:
        return check_send_delay(params, lookup)
    if action_type == ActionType.SUBMIT_LOAN_PACK:
        return check_submit_pack(params, lookup)
    if action_type == ActionType.CANCEL_PAYMENT:
        return check_cancel(params, executed or (lambda _a: None), today)
    if action_type == ActionType.DRAFT_REMINDER:
        _overdue_invoice(params.invoice_id, lookup, today)
        return ["CTL-AI-01"]
    if action_type == ActionType.DRAFT_DELAY_REQUEST:
        _delay_eligible_bill(params.bill_id, params.proposed_date, lookup, open_only=False)
        return []
    return []
