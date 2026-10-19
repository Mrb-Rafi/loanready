"""Draft creation: wording -> post-check -> fallback -> AI label -> hash (PRD/08 §3).

The AI label is the last line of the body, so ``body_sha256`` covers it (REG-AI-04).
Drafts are immutable: a new wording is a new draft with a new id and hash (OUT-03), so
an approval bound to the old hash can never send new text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from contracts.canonical import params_hash, sha256_hex
from contracts.dto_outputs import DelayFacts, MessageDraftDTO, ReminderFacts
from contracts.enums import DraftKind, GeneratedBy
from contracts.services import WordingProvider
from outputs.postcheck import check_delay, check_reminder
from outputs.templates import AI_LABEL_LLM, AI_LABEL_TEMPLATE, TemplateWording, ai_label


@dataclass(frozen=True)
class DraftResult:
    draft: MessageDraftDTO
    fallback_used: bool  # the orchestrator logs LLM_FALLBACK_USED when True
    problems: list[str]


def _labelled(body: str, by: GeneratedBy, business_name: str) -> tuple[str, str]:
    """Make the body end with exactly the right label; strip any other label first."""
    label = ai_label(by, business_name)
    text = body.rstrip()
    for other in (AI_LABEL_TEMPLATE, AI_LABEL_LLM.format(business_name=business_name)):
        if text.endswith(other):
            text = text[: -len(other)].rstrip()
    return f"{text}\n\n{label}", label


def _make(
    draft_id: str,
    kind: DraftKind,
    facts: ReminderFacts | DelayFacts,
    related_id: str,
    subject: str,
    body: str,
    by: GeneratedBy,
    now: datetime,
) -> MessageDraftDTO:
    body, label = _labelled(body, by, facts.business_name)
    return MessageDraftDTO(
        draft_id=draft_id,
        kind=kind,
        channel=facts.channel,
        to_ref=facts.to_ref,
        subject=subject,
        body=body,
        body_sha256=sha256_hex(body),
        facts_hash=params_hash(facts),
        generated_by=by,
        ai_label=label,
        related_id=related_id,
        created_at=now,
        status="draft",
        sbx_ref=None,
    )


def reminder_draft(
    facts: ReminderFacts, wording: WordingProvider, draft_id: str, now: datetime
) -> DraftResult:
    subject, body, by = wording.word_reminder(facts)
    problems = check_reminder(subject, body, facts)
    fallback = bool(problems)
    if fallback:
        subject, body, by = TemplateWording().word_reminder(facts)
    d = _make(draft_id, DraftKind.REMINDER, facts, facts.invoice_id, subject, body, by, now)
    return DraftResult(d, fallback, problems)


def delay_draft(
    facts: DelayFacts, wording: WordingProvider, draft_id: str, now: datetime
) -> DraftResult:
    subject, body, by = wording.word_delay_request(facts)
    problems = check_delay(subject, body, facts)
    fallback = bool(problems)
    if fallback:
        subject, body, by = TemplateWording().word_delay_request(facts)
    d = _make(draft_id, DraftKind.DELAY_REQUEST, facts, facts.bill_id, subject, body, by, now)
    return DraftResult(d, fallback, problems)
