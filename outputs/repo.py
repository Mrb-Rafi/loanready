"""SQL for the WP6-owned tables: outbox_drafts, loan_packs, lender_submissions (ARC-27).

Draft content is never updated; ``mark_sent`` changes only ``status`` and ``sbx_ref``.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import select

from contracts.dto_outputs import LoanPackDTO, MessageDraftDTO
from contracts.enums import Channel, DraftKind, GeneratedBy
from contracts.schema import LenderSubmission, LoanPackRow, OutboxDraft
from infra.db import session


def _draft(r: OutboxDraft) -> MessageDraftDTO:
    return MessageDraftDTO(
        draft_id=r.id,
        kind=DraftKind(r.kind),
        channel=Channel(r.channel),
        to_ref=r.to_ref,
        subject=r.subject,
        body=r.body,
        body_sha256=r.body_sha256,
        facts_hash=r.facts_hash,
        generated_by=GeneratedBy(r.generated_by),
        ai_label=r.ai_label,
        related_id=r.related_id,
        created_at=r.created_at,
        status=r.status,  # type: ignore[arg-type]
        sbx_ref=r.sbx_ref,
    )


def _pack(r: LoanPackRow) -> LoanPackDTO:
    return LoanPackDTO(
        pack_id=r.id,
        path=r.path,
        sha256=r.sha256,
        sections=json.loads(r.sections_json),
        created_at=r.created_at,
        metadata=json.loads(r.metadata_json),
    )


class OutputsRepo:
    def __init__(self, engine: Engine):
        self.engine = engine

    def add_draft(self, d: MessageDraftDTO) -> None:
        with session(eng=self.engine) as s:
            s.add(
                OutboxDraft(
                    id=d.draft_id,
                    kind=d.kind.value,
                    channel=d.channel.value,
                    to_ref=d.to_ref,
                    subject=d.subject,
                    body=d.body,
                    body_sha256=d.body_sha256,
                    facts_hash=d.facts_hash,
                    generated_by=d.generated_by.value,
                    ai_label=d.ai_label,
                    related_id=d.related_id,
                    status=d.status,
                    sbx_ref=d.sbx_ref,
                    created_at=d.created_at,
                )
            )

    def draft(self, draft_id: str) -> MessageDraftDTO | None:
        with session(eng=self.engine) as s:
            r = s.get(OutboxDraft, draft_id)
            return _draft(r) if r is not None else None

    def drafts(self) -> list[MessageDraftDTO]:
        with session(eng=self.engine) as s:
            rows = s.exec(select(OutboxDraft).order_by(OutboxDraft.created_at, OutboxDraft.id))
            return [_draft(r) for r in rows.all()]

    def mark_sent(self, draft_id: str, sbx_ref: str) -> None:
        with session(eng=self.engine) as s:
            r = s.get(OutboxDraft, draft_id)
            if r is None:
                raise KeyError(draft_id)
            r.status, r.sbx_ref = "sent", sbx_ref
            s.add(r)

    def add_pack(self, p: LoanPackDTO) -> None:
        with session(eng=self.engine) as s:
            s.add(
                LoanPackRow(
                    id=p.pack_id,
                    path=p.path,
                    sha256=p.sha256,
                    sections_json=json.dumps(p.sections),
                    metadata_json=json.dumps(p.metadata, sort_keys=True),
                    created_at=p.created_at,
                )
            )

    def pack(self, pack_id: str) -> LoanPackDTO | None:
        with session(eng=self.engine) as s:
            r = s.get(LoanPackRow, pack_id)
            return _pack(r) if r is not None else None

    def add_submission(
        self, sub_id: str, pack_id: str, sha256: str, lender_id: str, receipt_id: str, at: datetime
    ) -> None:
        with session(eng=self.engine) as s:
            s.add(
                LenderSubmission(
                    id=sub_id,
                    pack_id=pack_id,
                    pack_sha256=sha256,
                    lender_id=lender_id,
                    receipt_id=receipt_id,
                    submitted_at=at,
                )
            )

    def submissions(self, pack_id: str) -> list[tuple[str, str]]:
        """(lender_id, receipt_id) pairs for a pack, oldest first."""
        with session(eng=self.engine) as s:
            q = (
                select(LenderSubmission)
                .where(LenderSubmission.pack_id == pack_id)
                .order_by(LenderSubmission.submitted_at, LenderSubmission.id)
            )
            return [(r.lender_id, r.receipt_id) for r in s.exec(q).all()]
