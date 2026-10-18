"""ExecutorImpl (PRD/11 §4): the only code that turns an approved Action into a sandbox call.

Called **only by the gate** with a freshly minted ``ExecutionTicket`` (GOV-11); the
adapters verify that ticket against the outgoing request before anything leaves the
process. Every parameter comes from the approved ``action.params`` or from stored records
(drafts, packs), never from document text. The executor never edits the books: a settled
payment reaches the ledger through the bank feed on the next ``sync_all()``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from contracts.actions import Action, ExecutionResult, ExecutionTicket
from contracts.canonical import file_sha256, sha256_hex
from contracts.enums import ActionType, ReasonCode
from contracts.errors import EntityCheckFailed
from contracts.ports import Ports
from contracts.sandbox_api import LenderApplicationIn, MessageSendIn, PaymentScheduleIn
from contracts.services import BooksService, OutputsService
from orchestrator.assemble import pack_path

REFERENCE_MAX = 60  # PaymentScheduleIn.reference max length


@dataclass(frozen=True)
class PackRequest:
    """What the owner asked for when the pack was built (sent with the application)."""

    amount_cents: int
    term_days: int
    purpose: str


class ExecutorImpl:
    def __init__(
        self,
        books: BooksService,
        outputs: OutputsService,
        business_name: str,
        ports: Ports | None = None,
    ):
        self.books = books
        self.outputs = outputs
        self.business_name = business_name
        self.ports = ports  # set by the container after build_ports (needs the gate verifier)
        self.pack_requests: dict[str, PackRequest] = {}

    def _ports(self) -> Ports:
        if self.ports is None:
            raise RuntimeError("executor has no ports (container wiring incomplete)")
        return self.ports

    def execute(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        handler = {
            ActionType.SCHEDULE_SUPPLIER_PAYMENT: self._payment,
            ActionType.CANCEL_PAYMENT: self._cancel,
            ActionType.SEND_REMINDER: self._send,
            ActionType.SEND_DELAY_REQUEST: self._send,
            ActionType.SUBMIT_LOAN_PACK: self._submit_pack,
            ActionType.TEST_T3_NOTE: self._t3_note,
        }.get(action.type)
        if handler is None:
            return ExecutionResult(
                action_id=action.action_id,
                ok=False,
                sbx_ref=None,
                result={"error": f"no executor for {action.type.value}"},
                error_code=ReasonCode.INVALID_PARAMS,
            )
        return handler(action, ticket)

    # -- per type ---------------------------------------------------------------------------
    def _payment(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        p = action.params
        bill = self.books.bill(p["bill_id"])
        number = bill.number if bill else p["bill_id"]
        out = self._ports().bank.schedule_payment(
            ticket,
            PaymentScheduleIn(
                from_account_id=p["from_account_id"],
                payee_id=p["payee_id"],
                bank_account_ref=p["bank_account_ref"],
                amount_cents=p["amount_cents"],
                value_date=p["value_date"],
                reference=f"{number} via LoanReady"[:REFERENCE_MAX],
            ),
        )
        result: dict[str, Any] = {
            "status": str(out.status),
            "value_date": out.value_date.isoformat(),
            "amount_cents": out.amount_cents,
        }
        if str(out.status) == "failed":
            result["failure_code"] = out.failure_code
            return ExecutionResult(
                action_id=action.action_id,
                ok=False,
                sbx_ref=out.sbx_ref,
                result=result,
                error_code=ReasonCode.PAYMENT_FAILED,
            )
        return ExecutionResult(
            action_id=action.action_id, ok=True, sbx_ref=out.sbx_ref, result=result
        )

    def _cancel(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        out = self._ports().bank.cancel_payment(ticket, action.params["sbx_ref"])
        return ExecutionResult(
            action_id=action.action_id,
            ok=True,
            sbx_ref=out.sbx_ref,
            result={"status": str(out.status)},
        )

    def _send(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        p = action.params
        draft = self.outputs.draft(p["draft_id"])
        if draft is None:
            raise EntityCheckFailed("draft not found", reason_code=ReasonCode.DRAFT_MISMATCH)
        # defence in depth: the stored body must still hash to the approved value
        if sha256_hex(draft.body) != p["body_sha256"]:
            raise EntityCheckFailed(
                "draft body changed after approval", reason_code=ReasonCode.DRAFT_MISMATCH
            )
        msg = self._ports().messaging.send(
            ticket,
            MessageSendIn(
                channel=draft.channel.value,
                to_ref=draft.to_ref,
                subject=draft.subject,
                body=draft.body,
            ),
        )
        self.outputs.mark_sent(draft.draft_id, msg.sbx_ref)
        return ExecutionResult(
            action_id=action.action_id,
            ok=True,
            sbx_ref=msg.sbx_ref,
            result={"status": msg.status, "channel": msg.channel},
        )

    def _submit_pack(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        p = action.params
        pack = self.outputs.loan_pack(p["pack_id"])
        if pack is None:
            raise EntityCheckFailed("loan pack not found", reason_code=ReasonCode.UNKNOWN_ENTITY)
        path = pack_path(pack.path)
        pdf = path.read_bytes()
        if file_sha256(path) != p["pack_sha256"]:
            raise EntityCheckFailed(
                "loan pack file changed after approval",
                reason_code=ReasonCode.PACK_HASH_MISMATCH,
            )
        req = self.pack_requests.get(pack.pack_id) or PackRequest(
            amount_cents=int(pack.metadata.get("requested_amount_cents", 0)),
            term_days=int(pack.metadata.get("term_days", 90)),
            purpose=str(pack.metadata.get("purpose", "Working capital")),
        )
        rec = self._ports().lender.submit_application(
            ticket,
            LenderApplicationIn(
                lender_id=p["lender_id"],
                business_name=self.business_name,
                requested_amount_cents=req.amount_cents,
                term_days=req.term_days,
                purpose=req.purpose,
                pack_sha256=p["pack_sha256"],
                sections=list(pack.sections),
                pdf_base64=base64.b64encode(pdf).decode("ascii"),
            ),
        )
        self.outputs.record_submission(pack.pack_id, p["lender_id"], rec.receipt_id)
        return ExecutionResult(
            action_id=action.action_id,
            ok=True,
            sbx_ref=rec.receipt_id,
            result={"status": rec.status, "missing_sections": list(rec.missing_sections)},
        )

    def _t3_note(self, action: Action, ticket: ExecutionTicket) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            ok=True,
            sbx_ref=None,
            result={"note": str(action.params.get("note", ""))},
        )
