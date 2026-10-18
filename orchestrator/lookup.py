"""EntityLookupImpl (PRD/11 §1): the gate's read-only view of stored master data."""

from __future__ import annotations

from datetime import date

from contracts.dto_books import BillDTO, CounterpartyDTO, InvoiceDTO, PayeeDTO
from contracts.dto_outputs import LoanPackDTO, MessageDraftDTO
from contracts.services import BooksService, OutputsService


class EntityLookupImpl:
    """Books answers master-data questions; outputs answers drafts and loan packs."""

    def __init__(self, books: BooksService, outputs: OutputsService):
        self.books = books
        self.outputs = outputs

    def payee(self, payee_id: str) -> PayeeDTO | None:
        return self.books.payee(payee_id)

    def bill(self, bill_id: str) -> BillDTO | None:
        return self.books.bill(bill_id)

    def invoice(self, invoice_id: str) -> InvoiceDTO | None:
        return self.books.invoice(invoice_id)

    def counterparty(self, cp_id: str) -> CounterpartyDTO | None:
        return self.books.counterparty(cp_id)

    def draft(self, draft_id: str) -> MessageDraftDTO | None:
        return self.outputs.draft(draft_id)

    def loan_pack(self, pack_id: str) -> LoanPackDTO | None:
        return self.outputs.loan_pack(pack_id)

    def period_is_closed(self, d: date) -> bool:
        return self.books.period_is_closed(d)

    def reconciliation_open_items(self) -> int:
        return self.books.reconciliation_open_items()
