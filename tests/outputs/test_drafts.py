"""T-OUT-04 proves REG-AI-04: the AI label is the last line of the hashed body;
T-OUT-05 proves OUT-03: drafts are immutable; new wording = new draft id and hash."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from contracts.canonical import sha256_hex
from contracts.enums import DraftKind, GeneratedBy
from contracts.errors import InvalidParams
from contracts.testing.builders import make_bill, make_counterparty
from outputs.facts import reminder_facts
from outputs.templates import AI_LABEL_TEMPLATE, TemplateWording
from tests.outputs.test_postcheck import GOOD, LLMWording

AS_OF = date(2026, 10, 14)
LLM_LABEL = "Drafted with AI assistance by LoanReady; reviewed and approved by Tiny Bakery."


def test_template_label(outputs: Any, invoice: Any, customer: Any) -> None:
    """T-OUT-04: template drafts end with the template label, covered by body_sha256."""
    d = outputs.draft_reminder(
        reminder_facts(invoice, customer, AS_OF, "Tiny Bakery"), TemplateWording()
    )
    assert d.ai_label == AI_LABEL_TEMPLATE and d.body.endswith(AI_LABEL_TEMPLATE)
    assert d.body_sha256 == sha256_hex(d.body)
    assert d.body.count(AI_LABEL_TEMPLATE) == 1
    assert not outputs.last_fallback


def test_llm_label_appended(outputs: Any, invoice: Any, customer: Any) -> None:
    """T-OUT-04: an LLM wording gets the AI-assistance label as its last line."""
    f = reminder_facts(invoice, customer, AS_OF, "Tiny Bakery")
    d = outputs.draft_reminder(f, LLMWording(GOOD))
    assert d.generated_by == GeneratedBy.LLM
    assert d.ai_label == LLM_LABEL and d.body.endswith("\n\n" + LLM_LABEL)
    assert d.body_sha256 == sha256_hex(d.body)
    assert outputs.draft(d.draft_id) == d


def test_new_wording_new_draft(outputs: Any, invoice: Any, customer: Any) -> None:
    """T-OUT-05: a second wording creates a new draft; the first is unchanged."""
    f = reminder_facts(invoice, customer, AS_OF, "Tiny Bakery")
    first = outputs.draft_reminder(f, TemplateWording())
    second = outputs.draft_reminder(f, LLMWording(GOOD))
    assert first.draft_id != second.draft_id and first.body_sha256 != second.body_sha256
    assert first.facts_hash == second.facts_hash
    assert outputs.draft(first.draft_id) == first
    assert [d.draft_id for d in outputs.drafts()] == [first.draft_id, second.draft_id]


def test_mark_sent_only_status(outputs: Any, invoice: Any, customer: Any) -> None:
    """T-OUT-05: mark_sent sets status and sbx_ref only; drafts() lists sent drafts too."""
    d = outputs.draft_reminder(reminder_facts(invoice, customer, AS_OF, "B"), TemplateWording())
    outputs.mark_sent(d.draft_id, "msg_1")
    sent = outputs.draft(d.draft_id)
    assert (sent.status, sent.sbx_ref) == ("sent", "msg_1")
    assert sent.model_copy(update={"status": "draft", "sbx_ref": None}) == d
    assert [x.status for x in outputs.drafts()] == ["sent"]
    with pytest.raises(InvalidParams):
        outputs.mark_sent(d.draft_id, "msg_2")
    with pytest.raises(InvalidParams):
        outputs.mark_sent("drf_missing", "msg_3")


def test_delay_request_draft(outputs: Any) -> None:
    """T-OUT-05: delay-request drafts go through the same path."""
    supplier = make_counterparty(id="cpt_tiny_mills", name="Tiny Mills", contact_ref="b@m.test")
    bill = make_bill(late_penalty=False, grace_days=14)
    f = outputs.delay_facts(bill, supplier, bill.due_date + timedelta(days=10))
    d = outputs.draft_delay_request(f, TemplateWording())
    assert d.kind == DraftKind.DELAY_REQUEST and d.related_id == bill.id
    assert f.proposed_date_str in d.body and d.body.endswith(AI_LABEL_TEMPLATE)
    bad = outputs.draft_delay_request(f, LLMWording("Please wait, we pay later."))
    assert bad.generated_by == GeneratedBy.TEMPLATE and outputs.last_fallback
