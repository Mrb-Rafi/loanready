"""Turning user requests into Actions (PRD/11 §5).

ORC-03: every Action is ``origin=USER_REQUEST``, ``requested_by=AGENT``,
``on_behalf_of=session.current()``, with a fresh ``act`` id and ``clock.now()``.
ORC-04: parameters come from **stored** master data and carry trusted SYSTEM provenance.
Nothing here ever copies a value out of document text (a bill's ``claimed_bank_account_ref``
or description, a memo, an OCR line) into params.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from contracts.actions import AGENT, Action, ProvenanceRef
from contracts.clock import get_clock
from contracts.dto_books import BillDTO, InvoiceDTO, PayeeDTO
from contracts.dto_outputs import LoanPackDTO, MessageDraftDTO
from contracts.enums import ActionType, Origin, SourceKind
from contracts.ids import get_id_factory
from contracts.registry import LENDER_IDS, OPERATING_ACCOUNT_ID
from orchestrator.session import Session

DEFAULT_LENDER_ID = sorted(LENDER_IDS)[0]


def build_action(
    session: Session,
    type_: ActionType,
    params: dict[str, Any],
    reason: str,
    provenance: list[ProvenanceRef] | None = None,
) -> Action:
    """ORC-03."""
    return Action(
        action_id=get_id_factory().new("act"),
        type=type_,
        params=params,
        origin=Origin.USER_REQUEST,
        provenance=provenance or [],
        reason=reason,
        requested_by=AGENT,
        on_behalf_of=session.current(),
        requested_at=get_clock().now(),
    )


def trusted(ref_id: str, *fields: str) -> list[ProvenanceRef]:
    """Trusted SYSTEM provenance for params read from stored master data (ORC-04)."""
    return [
        ProvenanceRef(source=SourceKind.SYSTEM, ref_id=ref_id, field=f, trusted=True)
        for f in fields
    ]


def payment_params(bill: BillDTO, payee: PayeeDTO | None, value_date: date) -> dict[str, Any]:
    """SCHEDULE_SUPPLIER_PAYMENT params from the stored bill and the **stored** payee account.

    ``bill.claimed_bank_account_ref`` is never read here: the gate compares it with the
    stored account and blocks with ACCOUNT_CHANGED (S6).
    """
    return {
        "bill_id": bill.id,
        "payee_id": payee.id if payee else (bill.payee_id or ""),
        "amount_cents": bill.outstanding_cents,
        "value_date": value_date,
        "bank_account_ref": payee.bank_account_ref if payee else "",
        "from_account_id": OPERATING_ACCOUNT_ID,
    }


def reminder_send_params(draft: MessageDraftDTO, invoice: InvoiceDTO | None) -> dict[str, Any]:
    """SEND_REMINDER: recipient, channel and body hash from the stored draft; amount from the
    stored invoice (the gate re-checks both)."""
    return {
        "invoice_id": draft.related_id,
        "draft_id": draft.draft_id,
        "channel": draft.channel,
        "to_ref": draft.to_ref,
        "amount_cents": invoice.outstanding_cents if invoice and invoice.outstanding_cents else 1,
        "body_sha256": draft.body_sha256,
    }


def delay_send_params(draft: MessageDraftDTO, proposed_date: date) -> dict[str, Any]:
    return {
        "bill_id": draft.related_id,
        "draft_id": draft.draft_id,
        "channel": draft.channel,
        "to_ref": draft.to_ref,
        "proposed_date": proposed_date,
        "body_sha256": draft.body_sha256,
    }


def submit_pack_params(pack: LoanPackDTO, lender_id: str = DEFAULT_LENDER_ID) -> dict[str, Any]:
    """SUBMIT_LOAN_PACK binds the **stored** pack hash; the gate re-hashes the file."""
    return {"pack_id": pack.pack_id, "pack_sha256": pack.sha256, "lender_id": lender_id}
