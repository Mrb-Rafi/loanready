"""``Books``: the WP3 implementation of ``contracts.services.BooksService`` (PRD/05).

Construction (PRD/11 §2): ``Books(engine, clock, ids, settings)``.

Payment state comes from the bank feed: master-data invoices and bills are stored unpaid
and become PAID when their payment is matched in the bank feed (BKS-04) or recorded with
``record_customer_payment`` / ``record_bill_payment``. Extra methods for the orchestrator
(not in the protocol): ``load_opening_balances``, ``load_recurring``, ``load_bundle``,
``last_scan_flags``, ``accounting_policies``, ``receipts``, ``reverse_entry``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from books import analytics, confirm, duplicates, posting, receivables, statements
from books import receipts as rcpt
from books.categorise import categorise_text
from books.ingest import bank_raw, match_document, pos_raw, scan
from books.policies import accounting_policies
from books.reconcile import reconcile as reconcile_fn
from books.repo import BooksRepo, RawRow
from books.rules import FALLBACK_LABEL, RuleClassifier, load_rules, match_rule
from contracts.actions import Principal
from contracts.canonical import params_hash
from contracts.clock import Clock, month_key
from contracts.dto_books import (
    BalanceSheetDTO,
    BillDTO,
    CashFlowStatementDTO,
    CategorisationResult,
    ConfirmItemDTO,
    CounterpartyDTO,
    InvoiceDTO,
    JournalEntryDTO,
    JournalLineDTO,
    PayeeDTO,
    ProfitAndLossDTO,
    RawTransactionDTO,
    ReceiptDTO,
    ReceivablesAgeingDTO,
    ReconciliationDTO,
    RecurringObligation,
    WeeklySales,
)
from contracts.dto_financing import ReadinessInputs
from contracts.enums import (
    BillStatus,
    CategorisationMethod,
    ConfirmKind,
    EntrySource,
    EntryStatus,
    InvoiceStatus,
    OcrBackend,
    RawStatus,
    ReceiptStatus,
    SourceKind,
)
from contracts.errors import InvalidParams, PeriodLocked
from contracts.ids import IdFactory
from contracts.money import fmt
from contracts.registry import PERSONAL_LABEL
from contracts.sandbox_api import BankTxnOut, PosSaleOut
from contracts.seed import SeedBundle
from contracts.services import Classifier
from contracts.untrusted import ScanFlag

SYSTEM = "system"
MATCH = duplicates.DOCUMENT_MATCH_METHOD
OPEN_DOC = (InvoiceStatus.OPEN, InvoiceStatus.PARTIAL)


class Books:
    """Ledger, statements, reconciliation and receipts over ``var/app.db``."""

    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        ids: IdFactory,
        settings: Any,
        rules_path: Path | None = None,
    ):
        self.repo = BooksRepo(engine)
        self.clock = clock
        self.ids = ids
        self.settings = settings
        self.rules = load_rules(rules_path) if rules_path else load_rules()
        self._flags: list[ScanFlag] = []
        self.repo.ensure_accounts()

    # --- helpers -------------------------------------------------------------------------
    @property
    def framework(self) -> Any:
        return self.settings.framework

    def _entry(
        self, d: posting.EntryDraft, created_by: str, closed: set[str] | None = None
    ) -> JournalEntryDTO:
        """Give a draft its id; refuse a date in a closed period (``closed`` = the closed
        months, read once per batch by the caller)."""
        closed = self.repo.closed_months() if closed is None else closed
        if month_key(d.date) in closed:
            raise PeriodLocked(f"period {month_key(d.date)} is closed")
        return JournalEntryDTO(
            id=self.ids.new("je"),
            date=d.date,
            memo=d.memo,
            source=d.source,
            source_ref=d.source_ref,
            status=EntryStatus.POSTED,
            lines=list(d.lines),
            created_by=created_by,
            reversal_of=d.reversal_of,
        )

    def _post(self, d: posting.EntryDraft, created_by: str = SYSTEM, payload: Any = None) -> str:
        e = self._entry(d, created_by)
        self.repo.insert_entries([(e, params_hash({"payload": payload or d.source_ref}))])
        return e.id

    def _confirm_id(self) -> str:
        return self.ids.new("cfm")

    # --- master data ---------------------------------------------------------------------
    def load_master_data(
        self,
        counterparties: list[CounterpartyDTO],
        payees: list[PayeeDTO],
        invoices: list[InvoiceDTO],
        bills: list[BillDTO],
    ) -> None:
        """BKS-01: upsert parties and payees; a new invoice posts revenue (row 5) on its
        issue date, a new bill posts expense/AP (row 7). Re-loading is a no-op for
        documents already in the books."""
        self.repo.upsert_counterparties(counterparties)
        self.repo.upsert_payees(payees)
        batch: list[tuple[JournalEntryDTO, str]] = []
        closed = self.repo.closed_months()
        for inv in invoices:
            if self.repo.insert_invoice_if_new(inv) and inv.status != InvoiceStatus.VOID:
                draft = posting.invoice_issued(inv.issue_date, inv.id, inv.amount_cents, inv.number)
                batch.append((self._entry(draft, SYSTEM, closed), params_hash(inv)))
        for b in bills:
            if self.repo.insert_bill_if_new(b) and b.status != BillStatus.VOID:
                draft = posting.bill_received(
                    b.issue_date, b.id, b.amount_cents, b.number, b.category
                )
                batch.append((self._entry(draft, SYSTEM, closed), params_hash(b)))
        self.repo.insert_entries(batch)

    def load_opening_balances(self, lines: list[JournalLineDTO], d: date) -> None:
        """Row 14: one OPENING entry at ``history_start`` (idempotent)."""
        if not self.repo.entry_exists(EntrySource.OPENING, "opening"):
            self._post(posting.opening(d, lines), payload=[ln.model_dump() for ln in lines])

    def load_recurring(self, obligations: list[RecurringObligation]) -> None:
        def slug(name: str) -> str:
            return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]

        self.repo.upsert_recurring(
            (f"rec_{i:02d}_{slug(r.name)}", r) for i, r in enumerate(obligations, start=1)
        )

    def load_bundle(self, bundle: SeedBundle) -> None:
        """Master data + opening balances + recurring obligations from a seed bundle."""
        self.load_master_data(bundle.counterparties, bundle.payees, bundle.invoices, bundle.bills)
        self.load_opening_balances(bundle.opening_balances, bundle.history_start)
        self.load_recurring(bundle.recurring)

    # --- ingestion -----------------------------------------------------------------------
    def last_scan_flags(self) -> list[ScanFlag]:
        """Injection-scanner flags from the last ingest call (the orchestrator logs one
        UNTRUSTED_INSTRUCTION_IGNORED event per flagged item; BKS-02)."""
        return list(self._flags)

    def ingest_bank(self, txns: list[BankTxnOut]) -> list[RawTransactionDTO]:
        """BKS-02: new bank lines -> raw rows (status NEW); idempotent by (source, ref)."""
        seen = self.repo.existing_refs(SourceKind.BANK)
        rows: list[RawRow] = []
        self._flags = []
        for t in sorted(txns, key=lambda x: (x.date, x.id)):
            if t.id in seen:
                continue
            seen.add(t.id)
            row = bank_raw(t, self.ids)
            rows.append(row)
            self._flags += scan(row.description, SourceKind.BANK, t.id)
        self.repo.insert_raw(rows)
        return [r.dto() for r in rows]

    def ingest_pos(self, sales: list[PosSaleOut]) -> list[RawTransactionDTO]:
        """BKS-03: each new sale -> raw row, posted at once (rows 1-2). Notes are scanned."""
        seen = self.repo.existing_refs(SourceKind.POS)
        rows: list[RawRow] = []
        batch: list[tuple[JournalEntryDTO, str]] = []
        closed = self.repo.closed_months()
        self._flags = []
        for s in sorted(sales, key=lambda x: (x.ts, x.id)):
            if s.id in seen:
                continue
            seen.add(s.id)
            row = pos_raw(s, self.ids)
            entry = self._entry(
                posting.pos_sale(row.date, s.id, s.amount_cents, s.tender), SYSTEM, closed
            )
            batch.append((entry, row.payload_hash))
            rows.append(
                replace(
                    row,
                    status=RawStatus.POSTED,
                    label=posting.SALES,
                    method=CategorisationMethod.RULE.value,
                    confidence=Decimal(1),
                    journal_entry_id=entry.id,
                )
            )
            if s.note:
                self._flags += scan(str(s.note), SourceKind.POS, s.id)
        self.repo.insert_raw(rows)
        self.repo.insert_entries(batch)
        return [r.dto() for r in rows]

    # --- categorisation ------------------------------------------------------------------
    def categorise_pending(
        self, classifier: Classifier | None = None
    ) -> list[CategorisationResult]:
        """Document matching (BKS-04) -> duplicates (BKS-13) -> rules -> classifier
        (BKS-05/06) -> confirm queue below the threshold or for PERSONAL (BKS-05/08)."""
        clf = classifier or RuleClassifier()
        threshold = Decimal(self.settings.confidence_confirm_threshold)
        today = self.clock.today()
        parties = {c.id: c for c in self.repo.counterparties()}
        invoices = {i.id: i for i in self.repo.invoices()}
        bills = {b.id: b for b in self.repo.bills()}
        all_bank = self.repo.raw_rows(SourceKind.BANK)
        earlier = [r for r in all_bank if r.status != RawStatus.NEW]
        results: list[CategorisationResult] = []
        for row in [r for r in all_bank if r.status == RawStatus.NEW]:
            m = match_document(row, list(invoices.values()), list(bills.values()), parties, today)
            if m is not None:
                row = self._post_match(row, m, invoices, bills)
                results.append(
                    CategorisationResult(
                        raw_txn_id=row.id,
                        label=row.label or "",
                        confidence=Decimal(1),
                        method=CategorisationMethod.RULE,
                        needs_confirmation=False,
                        rule_id=f"MATCH-{m.kind.upper()}",
                    )
                )
                earlier.append(row)
                continue
            original = duplicates.find_original(
                row, [e for e in earlier if abs((e.date - row.date).days) <= duplicates.MAX_DAYS]
            )
            res = categorise_text(row.id, row.description, self.rules, clf, threshold)
            if original is not None:
                row = replace(
                    row,
                    status=RawStatus.DUPLICATE_SUSPECT,
                    label=res.label,
                    method=res.method.value,
                    confidence=res.confidence,
                )
                self.repo.add_confirm(
                    confirm.duplicate_item(
                        self._confirm_id(),
                        row.id,
                        row.description,
                        row.amount_cents,
                        original.source_ref,
                        self.clock.now(),
                    )
                )
                res = res.model_copy(update={"needs_confirmation": True})
            elif res.needs_confirmation:
                row = replace(
                    row,
                    status=RawStatus.PENDING_CONFIRMATION,
                    label=res.label,
                    method=res.method.value,
                    confidence=res.confidence,
                )
                self._ask(row, res)
            else:
                row = replace(
                    row,
                    status=RawStatus.CATEGORISED,
                    label=res.label,
                    method=res.method.value,
                    confidence=res.confidence,
                )
            self.repo.update_raw(row)
            earlier.append(row)
            results.append(res)
        return results

    def _ask(self, row: RawRow, res: CategorisationResult) -> None:
        now = self.clock.now()
        if res.label == PERSONAL_LABEL:
            item = confirm.personal_item(
                self._confirm_id(), row.id, row.description, row.amount_cents, res.confidence, now
            )
        else:
            item = confirm.category_item(
                self._confirm_id(),
                row.id,
                row.description,
                row.amount_cents,
                res.label,
                res.confidence,
                now,
            )
        self.repo.add_confirm(item)

    def _post_match(
        self,
        row: RawRow,
        m: Any,
        invoices: dict[str, InvoiceDTO],
        bills: dict[str, BillDTO],
    ) -> RawRow:
        if m.kind == "invoice":
            draft = posting.customer_payment(row.date, row.source_ref, row.amount_cents, m.number)
            label = posting.AR
            self.repo.add_invoice_payment(m.doc_id, row.amount_cents)
            inv = invoices[m.doc_id]
            paid = inv.paid_cents + row.amount_cents
            status = InvoiceStatus.PAID if paid >= inv.amount_cents else InvoiceStatus.PARTIAL
            invoices[m.doc_id] = inv.model_copy(update={"paid_cents": paid, "status": status})
        elif m.kind == "bill":
            draft = posting.bill_payment(row.date, row.source_ref, -row.amount_cents, m.number)
            label = posting.AP
            self.repo.add_bill_payment(m.doc_id, -row.amount_cents)
            b = bills[m.doc_id]
            paid = b.paid_cents - row.amount_cents
            bstatus = BillStatus.PAID if paid >= b.amount_cents else BillStatus.PARTIAL
            bills[m.doc_id] = b.model_copy(update={"paid_cents": paid, "status": bstatus})
        elif m.kind == "card_settlement":
            draft = posting.card_settlement(
                row.date, row.source_ref, row.amount_cents, row.description
            )
            label = posting.CARD_CLEARING
        else:
            draft = posting.cash_deposit(row.date, row.source_ref, row.amount_cents)
            label = posting.TILL
        entry_id = self._post(draft, payload=row.payload_hash)
        done = replace(
            row,
            status=RawStatus.POSTED,
            label=label,
            method=MATCH,
            confidence=Decimal(1),
            journal_entry_id=entry_id,
        )
        self.repo.update_raw(done)
        return done

    def post_ready(self) -> int:
        """Post every CATEGORISED bank row (rows 9, 10, 13); returns the count."""
        rows = self.repo.raw_rows(SourceKind.BANK, [RawStatus.CATEGORISED])
        for row in rows:
            self._post_categorised(row, row.label or FALLBACK_LABEL, SYSTEM)
        return len(rows)

    def _post_categorised(self, row: RawRow, label: str, by: str) -> None:
        draft = posting.bank_categorised(
            row.date, row.source_ref, row.amount_cents, label, row.description[:60]
        )
        entry_id = self._post(draft, created_by=by, payload=row.payload_hash)
        self.repo.update_raw(
            replace(row, status=RawStatus.POSTED, label=label, journal_entry_id=entry_id)
        )

    # --- confirm queue -------------------------------------------------------------------
    def confirm_queue(self) -> list[ConfirmItemDTO]:
        """Open items, sorted by the date of the underlying record (BKS-15)."""
        items = [i for i, _ in self.repo.confirm_items("open")]
        dates: dict[str, date] = {r.id: r.date for r in self.repo.raw_rows()}
        for rc in self.repo.receipts():
            dates[rc.id] = rc.date or self.clock.today()
        return sorted(items, key=lambda i: (dates.get(i.ref_id, self.clock.today()), i.id))

    def resolve(self, item_id: str, choice: str, principal: Principal) -> None:
        """BKS-15: a human resolves an item; the resulting entry is posted at once."""
        confirm.check_principal(principal)
        found = self.repo.confirm_item(item_id)
        if found is None:
            raise InvalidParams(f"unknown confirm item {item_id}")
        item, status = found
        if status != "open":
            raise InvalidParams(f"confirm item {item_id} is already resolved")
        confirm.check_choice(item, choice)
        if item.kind in (ConfirmKind.CATEGORY, ConfirmKind.PERSONAL):
            row = self._raw_or_fail(item.ref_id)
            row = replace(row, method=CategorisationMethod.OWNER.value, confidence=Decimal(1))
            self._post_categorised(row, choice, principal.id)
        elif item.kind == ConfirmKind.DUPLICATE:
            self._resolve_duplicate(self._raw_or_fail(item.ref_id), choice, principal)
        else:
            self._resolve_receipt(item.ref_id, choice, principal)
        self.repo.resolve_confirm(item_id, choice, principal.id, self.clock.now())

    def _raw_or_fail(self, raw_id: str) -> RawRow:
        row = self.repo.raw(raw_id)
        if row is None:
            raise InvalidParams(f"unknown raw transaction {raw_id}")
        return row

    def _resolve_duplicate(self, row: RawRow, choice: str, principal: Principal) -> None:
        """Row 11 for a refund claim (1110); a genuine second purchase is categorised by
        rule, or goes to the confirm queue if the rule is uncertain or personal."""
        if choice == confirm.REFUND:
            draft = posting.refund_claim(
                row.date, row.source_ref, -row.amount_cents, f"Refund claim: {row.description[:40]}"
            )
            entry_id = self._post(draft, created_by=principal.id, payload=row.payload_hash)
            self.repo.update_raw(
                replace(
                    row,
                    status=RawStatus.POSTED,
                    label=posting.REFUND_CLAIMS,
                    method=CategorisationMethod.OWNER.value,
                    journal_entry_id=entry_id,
                )
            )
            return
        rule = match_rule(self.rules, row.description)
        threshold = Decimal(self.settings.confidence_confirm_threshold)
        if rule is not None and rule.label != PERSONAL_LABEL and rule.confidence >= threshold:
            self._post_categorised(row, rule.label, principal.id)
            return
        label = rule.label if rule is not None else FALLBACK_LABEL
        conf = rule.confidence if rule is not None else Decimal(0)
        pending = replace(row, status=RawStatus.PENDING_CONFIRMATION, label=label)
        self.repo.update_raw(pending)
        res = CategorisationResult(
            raw_txn_id=row.id,
            label=label,
            confidence=conf,
            method=CategorisationMethod.RULE,
            needs_confirmation=True,
            rule_id=rule.id if rule else None,
        )
        self._ask(pending, res)

    def _resolve_receipt(self, receipt_id: str, choice: str, principal: Principal) -> None:
        rc = self.repo.receipt(receipt_id)
        if rc is None:
            raise InvalidParams(f"unknown receipt {receipt_id}")
        if choice == confirm.RECEIPT_REJECT:
            self.repo.set_receipt_status(receipt_id, ReceiptStatus.REJECTED)
            return
        if self._receipt_bank_match(rc) is None:
            if rc.total_cents is None:
                raise InvalidParams("receipt total unreadable; it cannot be posted")
            rule = match_rule(self.rules, rc.vendor or "")
            category = rule.label if rule and rule.label != PERSONAL_LABEL else FALLBACK_LABEL
            draft = posting.till_payment(
                rc.date or self.clock.today(),
                rc.id,
                rc.total_cents,
                category,
                f"Receipt {rc.vendor or ''}"[:60],
            )
            self._post(draft, created_by=principal.id, payload=rc.id)
        self.repo.set_receipt_status(receipt_id, ReceiptStatus.CONFIRMED)

    # --- receipts ------------------------------------------------------------------------
    def _receipt_bank_match(self, rc: ReceiptDTO) -> RawRow | None:
        bank = [r for r in self.repo.raw_rows(SourceKind.BANK) if r.amount_cents < 0]
        return rcpt.match_bank(rc.vendor, rc.total_cents, rc.date, bank, self.clock.today())

    def ingest_receipt(self, image_path: Path) -> ReceiptDTO:
        """BKS-23..27: sidecar OCR, parse, confidence, scan. A receipt that matches a bank
        line is linked as evidence and never posted again (BKS-14); an unmatched receipt
        waits in the confirm queue and is posted from the till (row 12) once confirmed.
        Low confidence always asks the owner (CTL-DQ-01)."""
        path = Path(image_path)
        key = path.as_posix()
        existing = self.repo.receipt_by_path(key)
        if existing is not None:
            return existing
        text, mean_conf = rcpt.read_sidecar(path)
        parsed = rcpt.parse_text(text, mean_conf)
        threshold = Decimal(self.settings.receipt_confirm_threshold)
        receipt_id = self.ids.new("rcp")
        found = rcpt.flags(text, receipt_id)
        self._flags = list(found)
        rc = ReceiptDTO(
            id=receipt_id,
            image_path=key,
            ocr_text=text,
            ocr_backend=OcrBackend.SIDECAR,
            vendor=parsed.vendor,
            total_cents=parsed.total_cents,
            date=parsed.date,
            confidence=parsed.confidence,
            status=rcpt.status_for(parsed.confidence, threshold),
            flags=found,
        )
        self.repo.insert_receipt(rc)
        matched = self._receipt_bank_match(rc)
        if rc.status == ReceiptStatus.NEEDS_CONFIRMATION or matched is None:
            total = fmt(parsed.total_cents) if parsed.total_cents is not None else "unreadable"
            summary = f"{parsed.vendor or 'unknown vendor'}, total {total}"
            self.repo.add_confirm(
                confirm.receipt_item(
                    self._confirm_id(), receipt_id, summary, parsed.confidence, self.clock.now()
                )
            )
        return rc

    def receipts(self) -> list[ReceiptDTO]:
        return self.repo.receipts()

    # --- payments recorded outside the bank feed ----------------------------------------
    def record_customer_payment(
        self, invoice_id: str, amount_cents: int, d: date, ref: str
    ) -> None:
        inv = self.invoice(invoice_id)
        if inv is None:
            raise InvalidParams(f"unknown invoice {invoice_id}")
        if amount_cents <= 0 or amount_cents > inv.outstanding_cents:
            raise InvalidParams("payment must be positive and at most the outstanding amount")
        self._post(posting.customer_payment(d, ref, amount_cents, inv.number, EntrySource.MANUAL))
        self.repo.add_invoice_payment(invoice_id, amount_cents)

    def record_bill_payment(self, bill_id: str, amount_cents: int, d: date, ref: str) -> None:
        b = self.bill(bill_id)
        if b is None:
            raise InvalidParams(f"unknown bill {bill_id}")
        if amount_cents <= 0 or amount_cents > b.outstanding_cents:
            raise InvalidParams("payment must be positive and at most the outstanding amount")
        self._post(posting.bill_payment(d, ref, amount_cents, b.number, EntrySource.MANUAL))
        self.repo.add_bill_payment(bill_id, amount_cents)

    def reverse_entry(self, entry_id: str, principal: Principal, memo: str) -> str:
        """Row 16 (CTL-FR-02): post the mirror entry and mark the original REVERSED."""
        original = next((e for e in self.repo.entries() if e.id == entry_id), None)
        if original is None:
            raise InvalidParams(f"unknown entry {entry_id}")
        if original.status == EntryStatus.REVERSED:
            raise InvalidParams(f"entry {entry_id} is already reversed")
        new_id = self._post(
            posting.reversal(original, self.clock.today(), memo), created_by=principal.id
        )
        self.repo.mark_reversed(entry_id)
        return new_id

    # --- reports -------------------------------------------------------------------------
    def journal(self, start: date | None = None, end: date | None = None) -> list[JournalEntryDTO]:
        return self.repo.entries(start, end)

    def profit_and_loss(self, start: date, end: date) -> ProfitAndLossDTO:
        return statements.profit_and_loss(self.repo.entries(), start, end, self.framework)

    def cash_flow_statement(self, start: date, end: date) -> CashFlowStatementDTO:
        return statements.cash_flow_statement(self.repo.entries(), start, end, self.framework)

    def balance_sheet(self, as_of: date) -> BalanceSheetDTO:
        return statements.balance_sheet(self.repo.entries(), as_of, self.framework)

    def _duplicate_pairs(self) -> list[list[str]]:
        bank = self.repo.raw_rows(SourceKind.BANK)
        pairs = []
        for row in bank:
            if row.status != RawStatus.DUPLICATE_SUSPECT:
                continue
            others = [r for r in bank if r.id != row.id and r.date <= row.date]
            original = duplicates.find_original(row, others)
            if original is not None:
                pairs.append([original.source_ref, row.source_ref])
        return pairs

    def reconcile(
        self, bank_txns: list[BankTxnOut], bank_balance_cents: int, as_of: date
    ) -> ReconciliationDTO:
        return reconcile_fn(
            self.repo.entries(), bank_txns, bank_balance_cents, as_of, self._duplicate_pairs()
        )

    def receivables_ageing(self, as_of: date) -> ReceivablesAgeingDTO:
        parties = {c.id: c for c in self.repo.counterparties()}
        return receivables.ageing(self.repo.invoices(), parties, as_of)

    def accounting_policies(self) -> list[str]:
        return accounting_policies(self.framework, getattr(self.settings, "currency", "USD"))

    # --- master data reads ---------------------------------------------------------------
    def open_invoices(self) -> list[InvoiceDTO]:
        return [i for i in self.repo.invoices() if i.status in OPEN_DOC]

    def open_bills(self) -> list[BillDTO]:
        return [b for b in self.repo.bills() if b.status in (BillStatus.OPEN, BillStatus.PARTIAL)]

    def counterparties(self) -> list[CounterpartyDTO]:
        return self.repo.counterparties()

    def payees(self) -> list[PayeeDTO]:
        return self.repo.payees()

    def payee(self, payee_id: str) -> PayeeDTO | None:
        return next((p for p in self.repo.payees() if p.id == payee_id), None)

    def bill(self, bill_id: str) -> BillDTO | None:
        return next((b for b in self.repo.bills() if b.id == bill_id), None)

    def invoice(self, invoice_id: str) -> InvoiceDTO | None:
        return next((i for i in self.repo.invoices() if i.id == invoice_id), None)

    def counterparty(self, cp_id: str) -> CounterpartyDTO | None:
        return next((c for c in self.repo.counterparties() if c.id == cp_id), None)

    def recurring_obligations(self) -> list[RecurringObligation]:
        return self.repo.recurring()

    # --- analytics -----------------------------------------------------------------------
    def _pos(self) -> list[RawRow]:
        return self.repo.raw_rows(SourceKind.POS)

    def weekly_sales(self, weeks: int, as_of: date) -> list[WeeklySales]:
        return analytics.weekly_sales(self._pos(), weeks, as_of)

    def cogs_ratio(self, weeks: int, as_of: date) -> Decimal:
        return analytics.cogs_ratio(self.repo.entries(), self._pos(), weeks, as_of)

    def cash_balance(self, as_of: date) -> int:
        """BKS-31: balance of 1000 + 1010 at ``as_of``."""
        return statements.cash_balance(self.repo.entries(), as_of)

    def readiness_inputs(self, as_of: date) -> ReadinessInputs:
        all_items = self.repo.confirm_items(status=None)
        personal = [(i, s) for i, s in all_items if i.kind == ConfirmKind.PERSONAL]
        resolved = sum(1 for _, s in personal if s == "resolved")
        open_count = sum(1 for _, s in all_items if s == "open")
        return analytics.readiness_inputs(
            as_of,
            self.repo.entries(),
            self.repo.raw_rows(),
            self.repo.invoices(),
            len(personal),
            resolved,
            open_count,
        )

    # --- periods (SHOULD, not built) and control helpers ---------------------------------
    def close_period(self, month: str, principal: Principal) -> None:
        raise NotImplementedError("period close (CTL-FR-03) is a SHOULD module, not built yet")

    def period_is_closed(self, d: date) -> bool:
        return month_key(d) in self.repo.closed_months()

    def reconciliation_open_items(self) -> int:
        """Bank lines not yet in the books (pending confirmation or duplicate suspects)."""
        return sum(1 for r in self.repo.raw_rows(SourceKind.BANK) if r.status != RawStatus.POSTED)
