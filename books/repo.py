"""SQL for the WP3-owned tables (PRD/02 §8, ARC-27). Every access goes through
``infra.db.session``.

Journal immutability (CTL-FR-02): this module never updates journal amounts and never
deletes journal rows. The only journal UPDATE is setting an entry's status to REVERSED
when its reversal is posted.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Engine, insert
from sqlmodel import select

from contracts.clock import get_clock
from contracts.dto_books import (
    BillDTO,
    ConfirmItemDTO,
    CounterpartyDTO,
    InvoiceDTO,
    JournalEntryDTO,
    JournalLineDTO,
    PayeeDTO,
    RawTransactionDTO,
    ReceiptDTO,
    RecurringObligation,
)
from contracts.enums import (
    BillStatus,
    ConfirmKind,
    CounterpartyKind,
    EntrySource,
    EntryStatus,
    InvoiceStatus,
    OcrBackend,
    PayeeStatus,
    RawStatus,
    ReceiptStatus,
    SourceKind,
)
from contracts.registry import CHART_OF_ACCOUNTS
from contracts.schema import (
    Account,
    Bill,
    ConfirmItem,
    Counterparty,
    Invoice,
    JournalEntry,
    JournalLine,
    Payee,
    Period,
    RawTransaction,
    Receipt,
    RecurringObligationRow,
)
from contracts.untrusted import ScanFlag
from infra.db import session


@dataclass(frozen=True)
class RawRow:
    """A raw_transactions row with the categorisation columns."""

    id: str
    source: SourceKind
    source_ref: str
    date: date
    amount_cents: int
    description: str
    counterparty_hint: str | None
    tender: str | None
    payload_hash: str
    status: RawStatus
    label: str | None
    method: str | None
    confidence: Decimal | None
    journal_entry_id: str | None

    def dto(self) -> RawTransactionDTO:
        return RawTransactionDTO(
            id=self.id,
            source=self.source,
            source_ref=self.source_ref,
            date=self.date,
            amount_cents=self.amount_cents,
            description=self.description,
            counterparty_hint=self.counterparty_hint,
            tender=self.tender,
            payload_hash=self.payload_hash,
            status=self.status,
        )


def _raw(r: RawTransaction) -> RawRow:
    return RawRow(
        id=r.id,
        source=SourceKind(r.source),
        source_ref=r.source_ref,
        date=r.date,
        amount_cents=r.amount_cents,
        description=r.description,
        counterparty_hint=r.counterparty_hint,
        tender=r.tender,
        payload_hash=r.payload_hash,
        status=RawStatus(r.status),
        label=r.category_label,
        method=r.category_method,
        confidence=Decimal(r.category_confidence) if r.category_confidence else None,
        journal_entry_id=r.journal_entry_id,
    )


class BooksRepo:
    def __init__(self, engine: Engine):
        self.engine = engine
        self._journal: list[JournalEntryDTO] | None = None  # read cache, cleared on writes

    # --- chart of accounts -----------------------------------------------------------
    def ensure_accounts(self) -> None:
        with session(eng=self.engine) as s:
            have = {a.code for a in s.exec(select(Account)).all()}
            for a in CHART_OF_ACCOUNTS:
                if a.code not in have:
                    s.add(Account(code=a.code, name=a.name, type=a.type.value, is_cash=a.is_cash))

    # --- master data -----------------------------------------------------------------
    def upsert_counterparties(self, items: Iterable[CounterpartyDTO]) -> None:
        with session(eng=self.engine) as s:
            for c in items:
                row = s.get(Counterparty, c.id) or Counterparty(id=c.id, name=c.name, kind=c.kind)
                row.name, row.kind = c.name, c.kind.value
                row.payment_terms_days = c.payment_terms_days
                row.lateness_history_json = json.dumps(c.lateness_history_days)
                row.contact_ref = c.contact_ref
                s.add(row)

    def upsert_payees(self, items: Iterable[PayeeDTO]) -> None:
        with session(eng=self.engine) as s:
            for p in items:
                row = s.get(Payee, p.id) or Payee(
                    id=p.id,
                    counterparty_id=p.counterparty_id,
                    name=p.name,
                    bank_account_ref=p.bank_account_ref,
                    status=p.status.value,
                )
                row.counterparty_id, row.name = p.counterparty_id, p.name
                row.bank_account_ref, row.first_paid_on = p.bank_account_ref, p.first_paid_on
                row.status, row.preregistered_by_owner = p.status.value, p.preregistered_by_owner
                s.add(row)

    def insert_invoice_if_new(self, inv: InvoiceDTO) -> bool:
        """Insert an invoice with ``paid_cents = 0`` (payments come from the bank feed).
        Returns False when it already exists (its books payment state is kept)."""
        with session(eng=self.engine) as s:
            if s.get(Invoice, inv.id) is not None:
                return False
            status = InvoiceStatus.VOID if inv.status == InvoiceStatus.VOID else InvoiceStatus.OPEN
            s.add(
                Invoice(
                    id=inv.id,
                    number=inv.number,
                    customer_id=inv.customer_id,
                    issue_date=inv.issue_date,
                    due_date=inv.due_date,
                    amount_cents=inv.amount_cents,
                    paid_cents=0,
                    status=status.value,
                )
            )
            return True

    def insert_bill_if_new(self, b: BillDTO) -> bool:
        """Insert a bill with ``paid_cents = 0``; see ``insert_invoice_if_new``."""
        with session(eng=self.engine) as s:
            if s.get(Bill, b.id) is not None:
                return False
            status = BillStatus.VOID if b.status == BillStatus.VOID else BillStatus.OPEN
            s.add(
                Bill(
                    id=b.id,
                    number=b.number,
                    supplier_id=b.supplier_id,
                    payee_id=b.payee_id,
                    issue_date=b.issue_date,
                    due_date=b.due_date,
                    amount_cents=b.amount_cents,
                    paid_cents=0,
                    status=status.value,
                    category=b.category,
                    late_penalty=b.late_penalty,
                    grace_days=b.grace_days,
                    essential=b.essential,
                    claimed_bank_account_ref=b.claimed_bank_account_ref,
                    description=b.description,
                )
            )
            return True

    def add_invoice_payment(self, invoice_id: str, cents: int) -> None:
        with session(eng=self.engine) as s:
            row = s.get(Invoice, invoice_id)
            if row is None:
                raise KeyError(invoice_id)
            row.paid_cents += cents
            row.status = (
                InvoiceStatus.PAID if row.paid_cents >= row.amount_cents else InvoiceStatus.PARTIAL
            ).value
            s.add(row)

    def add_bill_payment(self, bill_id: str, cents: int) -> None:
        with session(eng=self.engine) as s:
            row = s.get(Bill, bill_id)
            if row is None:
                raise KeyError(bill_id)
            row.paid_cents += cents
            row.status = (
                BillStatus.PAID if row.paid_cents >= row.amount_cents else BillStatus.PARTIAL
            ).value
            s.add(row)

    def counterparties(self) -> list[CounterpartyDTO]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(Counterparty).order_by(Counterparty.id)).all()
            return [
                CounterpartyDTO(
                    id=r.id,
                    name=r.name,
                    kind=CounterpartyKind(r.kind),
                    payment_terms_days=r.payment_terms_days,
                    lateness_history_days=json.loads(r.lateness_history_json),
                    contact_ref=r.contact_ref,
                )
                for r in rows
            ]

    def payees(self) -> list[PayeeDTO]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(Payee).order_by(Payee.id)).all()
            return [
                PayeeDTO(
                    id=r.id,
                    counterparty_id=r.counterparty_id,
                    name=r.name,
                    bank_account_ref=r.bank_account_ref,
                    first_paid_on=r.first_paid_on,
                    status=PayeeStatus(r.status),
                    preregistered_by_owner=r.preregistered_by_owner,
                )
                for r in rows
            ]

    def invoices(self) -> list[InvoiceDTO]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(Invoice).order_by(Invoice.issue_date, Invoice.number)).all()
            return [
                InvoiceDTO(
                    id=r.id,
                    number=r.number,
                    customer_id=r.customer_id,
                    issue_date=r.issue_date,
                    due_date=r.due_date,
                    amount_cents=r.amount_cents,
                    paid_cents=r.paid_cents,
                    status=InvoiceStatus(r.status),
                )
                for r in rows
            ]

    def bills(self) -> list[BillDTO]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(Bill).order_by(Bill.issue_date, Bill.number)).all()
            return [
                BillDTO(
                    id=r.id,
                    number=r.number,
                    supplier_id=r.supplier_id,
                    payee_id=r.payee_id,
                    issue_date=r.issue_date,
                    due_date=r.due_date,
                    amount_cents=r.amount_cents,
                    paid_cents=r.paid_cents,
                    status=BillStatus(r.status),
                    category=r.category,
                    late_penalty=r.late_penalty,
                    grace_days=r.grace_days,
                    essential=r.essential,
                    claimed_bank_account_ref=r.claimed_bank_account_ref,
                    description=r.description,
                )
                for r in rows
            ]

    def upsert_recurring(self, items: Iterable[tuple[str, RecurringObligation]]) -> None:
        with session(eng=self.engine) as s:
            for rid, r in items:
                row = s.get(RecurringObligationRow, rid) or RecurringObligationRow(
                    id=rid,
                    name=r.name,
                    category=r.category,
                    amount_cents=r.amount_cents,
                    first_date=r.first_date,
                    schedule=r.schedule,
                )
                row.name, row.category, row.amount_cents = r.name, r.category, r.amount_cents
                row.first_date, row.schedule = r.first_date, r.schedule
                row.day_of_month, row.every_days = r.day_of_month, r.every_days
                row.end_date, row.essential = r.end_date, r.essential
                s.add(row)

    def recurring(self) -> list[RecurringObligation]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(RecurringObligationRow).order_by(RecurringObligationRow.id)).all()
            return [
                RecurringObligation(
                    name=r.name,
                    category=r.category,
                    amount_cents=r.amount_cents,
                    first_date=r.first_date,
                    schedule=r.schedule,  # type: ignore[arg-type]
                    day_of_month=r.day_of_month,
                    every_days=r.every_days,
                    end_date=r.end_date,
                    essential=r.essential,
                )
                for r in rows
            ]

    # --- raw transactions ------------------------------------------------------------
    def existing_refs(self, source: SourceKind) -> set[str]:
        with session(eng=self.engine) as s:
            refs = s.exec(
                select(RawTransaction.source_ref).where(RawTransaction.source == source.value)
            ).all()
            return set(refs)

    def insert_raw(self, rows: Iterable[RawRow]) -> None:
        now = get_clock().now()
        values = [
            {
                "id": r.id,
                "source": r.source.value,
                "source_ref": r.source_ref,
                "date": r.date,
                "amount_cents": r.amount_cents,
                "description": r.description,
                "counterparty_hint": r.counterparty_hint,
                "tender": r.tender,
                "payload_hash": r.payload_hash,
                "status": r.status.value,
                "category_label": r.label,
                "category_method": r.method,
                "category_confidence": str(r.confidence) if r.confidence is not None else None,
                "journal_entry_id": r.journal_entry_id,
                "created_at": now,
            }
            for r in rows
        ]
        if not values:
            return
        with session(eng=self.engine) as s:
            s.execute(insert(RawTransaction.__table__), values)  # type: ignore[attr-defined]

    def raw_rows(
        self, source: SourceKind | None = None, statuses: Iterable[RawStatus] | None = None
    ) -> list[RawRow]:
        with session(eng=self.engine) as s:
            q = select(RawTransaction)
            if source is not None:
                q = q.where(RawTransaction.source == source.value)
            if statuses is not None:
                q = q.where(RawTransaction.status.in_([st.value for st in statuses]))  # type: ignore[attr-defined]
            q = q.order_by(RawTransaction.date, RawTransaction.source_ref)
            return [_raw(r) for r in s.exec(q).all()]

    def raw(self, raw_id: str) -> RawRow | None:
        with session(eng=self.engine) as s:
            r = s.get(RawTransaction, raw_id)
            return _raw(r) if r is not None else None

    def update_raw(self, row: RawRow) -> None:
        """Categorisation state only (status, label, method, confidence, entry link)."""
        with session(eng=self.engine) as s:
            r = s.get(RawTransaction, row.id)
            if r is None:
                raise KeyError(row.id)
            r.status = row.status.value
            r.category_label = row.label
            r.category_method = row.method
            r.category_confidence = str(row.confidence) if row.confidence is not None else None
            r.journal_entry_id = row.journal_entry_id
            s.add(r)

    # --- journal ---------------------------------------------------------------------
    def entry_exists(self, source: EntrySource, source_ref: str) -> bool:
        with session(eng=self.engine) as s:
            q = select(JournalEntry.id).where(
                JournalEntry.source == source.value, JournalEntry.source_ref == source_ref
            )
            return s.exec(q).first() is not None

    def insert_entries(self, entries: Iterable[tuple[JournalEntryDTO, str]]) -> None:
        """Insert (entry, payload_hash) pairs with their lines in one unit of work (bulk
        INSERTs: heads first, then lines)."""
        items = list(entries)
        if not items:
            return
        now = get_clock().now()
        heads = [
            {
                "id": e.id,
                "date": e.date,
                "memo": e.memo,
                "source": e.source.value,
                "source_ref": e.source_ref,
                "status": e.status.value,
                "created_by": e.created_by,
                "reversal_of": e.reversal_of,
                "payload_hash": payload_hash,
                "created_at": now,
            }
            for e, payload_hash in items
        ]
        lines = [
            {
                "entry_id": e.id,
                "account_code": ln.account_code,
                "debit_cents": ln.debit_cents,
                "credit_cents": ln.credit_cents,
                "created_at": now,
            }
            for e, _ in items
            for ln in e.lines
        ]
        with session(eng=self.engine) as s:
            s.execute(insert(JournalEntry.__table__), heads)  # type: ignore[attr-defined]
            s.execute(insert(JournalLine.__table__), lines)  # type: ignore[attr-defined]
        self._journal = None

    def mark_reversed(self, entry_id: str) -> None:
        """The one permitted journal UPDATE: status -> REVERSED (amounts never change)."""
        with session(eng=self.engine) as s:
            row = s.get(JournalEntry, entry_id)
            if row is None:
                raise KeyError(entry_id)
            row.status = EntryStatus.REVERSED.value
            s.add(row)
        self._journal = None

    def entries(self, start: date | None = None, end: date | None = None) -> list[JournalEntryDTO]:
        """Journal entries (with lines) dated in [start, end], ordered by date and id. The
        full journal is read once and cached until the next journal write."""
        if self._journal is None:
            self._journal = self._load_journal()
        return [
            e
            for e in self._journal
            if (start is None or e.date >= start) and (end is None or e.date <= end)
        ]

    def _load_journal(self) -> list[JournalEntryDTO]:
        with session(eng=self.engine) as s:
            heads = s.exec(select(JournalEntry).order_by(JournalEntry.date, JournalEntry.id)).all()
            lines: dict[str, list[JournalLineDTO]] = {}
            for ln in s.exec(select(JournalLine).order_by(JournalLine.id)).all():
                lines.setdefault(ln.entry_id, []).append(
                    JournalLineDTO(
                        account_code=ln.account_code,
                        debit_cents=ln.debit_cents,
                        credit_cents=ln.credit_cents,
                    )
                )
            return [
                JournalEntryDTO(
                    id=h.id,
                    date=h.date,
                    memo=h.memo,
                    source=EntrySource(h.source),
                    source_ref=h.source_ref,
                    status=EntryStatus(h.status),
                    lines=lines.get(h.id, []),
                    created_by=h.created_by,
                    reversal_of=h.reversal_of,
                )
                for h in heads
            ]

    # --- receipts --------------------------------------------------------------------
    def receipt_by_path(self, image_path: str) -> ReceiptDTO | None:
        with session(eng=self.engine) as s:
            r = s.exec(select(Receipt).where(Receipt.image_path == image_path)).first()
            return _receipt(r) if r is not None else None

    def receipt(self, receipt_id: str) -> ReceiptDTO | None:
        with session(eng=self.engine) as s:
            r = s.get(Receipt, receipt_id)
            return _receipt(r) if r is not None else None

    def receipts(self) -> list[ReceiptDTO]:
        with session(eng=self.engine) as s:
            return [_receipt(r) for r in s.exec(select(Receipt).order_by(Receipt.id)).all()]

    def insert_receipt(self, rc: ReceiptDTO) -> None:
        with session(eng=self.engine) as s:
            s.add(
                Receipt(
                    id=rc.id,
                    image_path=rc.image_path,
                    ocr_text=rc.ocr_text,
                    ocr_backend=rc.ocr_backend.value,
                    vendor=rc.vendor,
                    total_cents=rc.total_cents,
                    date=rc.date,
                    confidence=str(rc.confidence),
                    status=rc.status.value,
                    flags_json=json.dumps(
                        [
                            {
                                "pattern_id": f.pattern_id,
                                "excerpt": f.excerpt,
                                "source": f.source.value,
                                "source_ref": f.source_ref,
                            }
                            for f in rc.flags
                        ]
                    ),
                )
            )

    def set_receipt_status(self, receipt_id: str, status: ReceiptStatus) -> None:
        with session(eng=self.engine) as s:
            r = s.get(Receipt, receipt_id)
            if r is None:
                raise KeyError(receipt_id)
            r.status = status.value
            s.add(r)

    # --- confirm queue ---------------------------------------------------------------
    def add_confirm(self, item: ConfirmItemDTO) -> None:
        with session(eng=self.engine) as s:
            s.add(
                ConfirmItem(
                    id=item.id,
                    kind=item.kind.value,
                    ref_id=item.ref_id,
                    question=item.question,
                    proposed=item.proposed,
                    options_json=json.dumps(item.options),
                    confidence=str(item.confidence) if item.confidence is not None else None,
                    created_at=item.created_at,
                )
            )

    def confirm_items(self, status: str | None = "open") -> list[tuple[ConfirmItemDTO, str]]:
        """(item, status) pairs, oldest first."""
        with session(eng=self.engine) as s:
            q = select(ConfirmItem)
            if status is not None:
                q = q.where(ConfirmItem.status == status)
            rows = s.exec(q.order_by(ConfirmItem.created_at, ConfirmItem.id)).all()
            return [(_confirm(r), r.status) for r in rows]

    def confirm_item(self, item_id: str) -> tuple[ConfirmItemDTO, str] | None:
        with session(eng=self.engine) as s:
            r = s.get(ConfirmItem, item_id)
            return (_confirm(r), r.status) if r is not None else None

    def resolve_confirm(self, item_id: str, choice: str, by: str, at: datetime) -> None:
        with session(eng=self.engine) as s:
            r = s.get(ConfirmItem, item_id)
            if r is None:
                raise KeyError(item_id)
            r.status, r.resolution, r.resolved_by, r.resolved_at = "resolved", choice, by, at
            s.add(r)

    # --- periods ---------------------------------------------------------------------
    def closed_months(self) -> set[str]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(Period).where(Period.status == "closed")).all()
            return {p.month for p in rows}


def _receipt(r: Receipt) -> ReceiptDTO:
    flags = [
        ScanFlag(f["pattern_id"], f["excerpt"], SourceKind(f["source"]), f["source_ref"])
        for f in json.loads(r.flags_json)
    ]
    return ReceiptDTO(
        id=r.id,
        image_path=r.image_path,
        ocr_text=r.ocr_text,
        ocr_backend=OcrBackend(r.ocr_backend),
        vendor=r.vendor,
        total_cents=r.total_cents,
        date=r.date,
        confidence=Decimal(r.confidence),
        status=ReceiptStatus(r.status),
        flags=flags,
    )


def _confirm(r: ConfirmItem) -> ConfirmItemDTO:
    return ConfirmItemDTO(
        id=r.id,
        kind=ConfirmKind(r.kind),
        ref_id=r.ref_id,
        question=r.question,
        proposed=r.proposed,
        options=json.loads(r.options_json),
        confidence=Decimal(r.confidence) if r.confidence else None,
        created_at=r.created_at,
    )
