"""Pull POS and bank data into the books, scan untrusted text and log flags (PRD/11 §1).

Untrusted text (bank memos, POS notes, bill descriptions, OCR) is scanned for visibility
only: each flagged text logs one ``UNTRUSTED_INSTRUCTION_IGNORED`` event. Nothing in this
module builds an Action, so no text can start or shape one (CLAUDE.md rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from contracts.dto_books import RawTransactionDTO, ReceiptDTO
from contracts.enums import ConfirmKind, EventType, SourceKind
from contracts.ports import Ports
from contracts.registry import OPERATING_ACCOUNT_ID
from contracts.services import AgentService, BooksService
from contracts.untrusted import as_untrusted
from orchestrator.events import EventSink

SYNC_LOOKBACK_DAYS = 400  # covers the 6-month seed history with room to spare


@dataclass
class SyncResult:
    bank: int = 0
    pos: int = 0
    ingested: int = 0
    flags: int = 0
    categorised: int = 0
    posted: int = 0
    duplicates: int = 0
    confirm_queue: int = 0
    raw_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "bank": self.bank,
            "pos": self.pos,
            "ingested": self.ingested,
            "flags": self.flags,
            "categorised": self.categorised,
            "posted": self.posted,
            "duplicates": self.duplicates,
            "confirm_queue": self.confirm_queue,
        }


def scan_raw(agent: AgentService, sink: EventSink, raw: RawTransactionDTO) -> int:
    text = as_untrusted(str(raw.description or ""), raw.source, raw.source_ref)
    return sink.flags(agent.scan(text), raw.source.value, raw.source_ref)


def scan_bills(agent: AgentService, sink: EventSink, books: BooksService) -> int:
    """Bill descriptions and claimed accounts come from documents (S6, S7 d)."""
    n = 0
    for bill in books.open_bills():
        for value in (bill.description, bill.claimed_bank_account_ref):
            if value:
                text = as_untrusted(str(value), SourceKind.BILL, bill.id)
                n += sink.flags(agent.scan(text), SourceKind.BILL.value, bill.id)
    return n


def scan_receipt(sink: EventSink, receipt: ReceiptDTO) -> int:
    """Books scans OCR text itself (ReceiptDTO.flags); the orchestrator logs them."""
    return sink.flags(receipt.flags, SourceKind.RECEIPT.value, receipt.id)


def log_duplicates(sink: EventSink, books: BooksService) -> int:
    n = 0
    for item in books.confirm_queue():
        if item.kind == ConfirmKind.DUPLICATE:
            seq = sink.log(
                EventType.DUPLICATE_FLAGGED,
                {"source": "CONFIRM", "source_ref": item.ref_id, "item_id": item.id},
            )
            n += seq is not None
    return n


def pull_and_ingest(
    books: BooksService,
    ports: Ports,
    agent: AgentService,
    sink: EventSink,
    today: date,
    start: date | None = None,
) -> SyncResult:
    """Steps 1-3 of ``sync_all``: pull, ingest (idempotent in books), scan and log flags."""
    start = start or today - timedelta(days=SYNC_LOOKBACK_DAYS)
    res = SyncResult()
    txns = ports.bank.list_transactions(OPERATING_ACCOUNT_ID, start, today)
    sales = ports.pos.list_sales(start, today)
    res.bank, res.pos = len(txns), len(sales)
    raws = [*books.ingest_bank(txns), *books.ingest_pos(sales)]
    res.ingested = len(raws)
    res.raw_ids = [r.id for r in raws]
    for raw in raws:
        res.flags += scan_raw(agent, sink, raw)
    res.flags += scan_bills(agent, sink, books)
    return res


def categorise_and_post(
    books: BooksService, agent: AgentService, sink: EventSink, res: SyncResult
) -> SyncResult:
    """Steps 4-5: classify pending raws (agent or rules), post ready items, log duplicates."""
    res.categorised = len(books.categorise_pending(agent.classifier()))
    res.posted = books.post_ready()
    res.duplicates = log_duplicates(sink, books)
    res.confirm_queue = len(books.confirm_queue())
    return res
