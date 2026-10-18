"""HttpLenderPort (LenderPort over HTTP). The ticket binds the SHA-256 of the decoded PDF
actually being sent and the lender id (SBX-A-03)."""

from __future__ import annotations

import base64
import binascii

from adapters.http import ReadClient, WriteClient
from contracts.actions import ExecutionTicket
from contracts.canonical import bytes_sha256
from contracts.enums import ActionType
from contracts.errors import TicketInvalid
from contracts.ports import TicketVerifier
from contracts.sandbox_api import LenderApplicationIn, LenderReceiptOut


class HttpLenderPort:
    def __init__(self, read: ReadClient, write: WriteClient, verifier: TicketVerifier):
        self._read, self._write, self._verify = read, write, verifier

    def submit_application(
        self, ticket: ExecutionTicket, req: LenderApplicationIn
    ) -> LenderReceiptOut:
        try:
            pdf = base64.b64decode(req.pdf_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise TicketInvalid("loan pack payload is not valid base64") from exc
        self._verify(
            ticket,
            ActionType.SUBMIT_LOAN_PACK,
            {"pack_sha256": bytes_sha256(pdf), "lender_id": req.lender_id},
        )
        return LenderReceiptOut.model_validate(
            self._write.post("/lender/applications", ticket, json=req.model_dump(mode="json"))
        )

    def get_application(self, receipt_id: str) -> LenderReceiptOut:
        return LenderReceiptOut.model_validate(self._read.get(f"/lender/applications/{receipt_id}"))
