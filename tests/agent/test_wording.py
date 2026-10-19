"""LLM wording (AGT-16): reminders and delay requests, facts only, masked names."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from agent.llm import LLM
from agent.wording import LLMWording
from contracts.dto_outputs import DelayFacts, ReminderFacts
from contracts.enums import Channel, GeneratedBy, LLMBackend, Tone
from contracts.services import WordingProvider


def _reminder(tone: Tone = Tone.FRIENDLY, days: int = 5) -> ReminderFacts:
    return ReminderFacts(
        invoice_id="inv_1",
        invoice_number="INV-1043",
        customer_name="Cafe Aurora",
        to_ref="cafe.aurora@example.test",
        channel=Channel.EMAIL,
        amount_outstanding_cents=480000,
        amount_str="$4,800.00",
        currency="USD",
        due_date=date(2026, 10, 9),
        due_date_str="9 Oct 2026",
        days_overdue=days,
        tone=tone,
        business_name="Tiny Bakery",
    )


def _delay() -> DelayFacts:
    return DelayFacts(
        bill_id="bill_1",
        bill_number="GM-2201",
        supplier_name="Golden Mills",
        to_ref="ap@goldenmills.test",
        channel=Channel.EMAIL,
        amount_cents=500000,
        amount_str="$5,000.00",
        due_date=date(2026, 10, 20),
        proposed_date=date(2026, 10, 27),
        due_date_str="20 Oct 2026",
        proposed_date_str="27 Oct 2026",
        business_name="Tiny Bakery",
    )


def test_satisfies_protocol(events: Any) -> None:
    """T-AGT-02 proves AGT-16: LLMWording implements contracts.services.WordingProvider."""
    assert isinstance(LLMWording(LLM(mode="mock", events=events)), WordingProvider)


@pytest.mark.parametrize(
    ("tone", "days", "subject"),
    [
        (Tone.FRIENDLY, 5, "Friendly reminder: invoice INV-1043"),
        (Tone.FIRM, 10, "Invoice INV-1043 is overdue"),
        (Tone.FINAL, 30, "Final reminder: invoice INV-1043"),
    ],
)
def test_mock_reminder_matches_tone_and_facts(
    tone: Tone, days: int, subject: str, events: Any
) -> None:
    """T-AGT-02 proves AGT-16, CTL-AI-01: mock wording uses the facts verbatim, per tone, and
    reports TEMPLATE (it is a template, so WP6 labels it as one)."""
    s, body, by = LLMWording(LLM(mode="mock", events=events)).word_reminder(_reminder(tone, days))
    assert s == subject
    for needle in ("INV-1043", "$4,800.00", "9 Oct 2026", "Cafe Aurora", "Tiny Bakery"):
        assert needle in s + body
    assert by == GeneratedBy.TEMPLATE
    assert body.rstrip().endswith("Tiny Bakery")


def test_mock_delay_request(events: Any) -> None:
    """T-AGT-02 proves AGT-16: the delay request carries every required fact."""
    s, body, by = LLMWording(LLM(mode="mock", events=events)).word_delay_request(_delay())
    assert s == "Request to adjust payment date for GM-2201"
    for needle in ("GM-2201", "$5,000.00", "20 Oct 2026", "27 Oct 2026", "Golden Mills"):
        assert needle in body
    assert by == GeneratedBy.TEMPLATE


def test_contact_ref_never_in_prompt(events: Any, stub: Any) -> None:
    """T-AGT-02 proves AGT-16, CTL-AI-02: the contact ref (C3) is never put in the prompt."""
    backend = stub(LLMBackend.OLLAMA, ['{"subject": "S INV-1043", "body": "B"}'])
    llm = LLM(mode="ollama", events=events, backends={LLMBackend.OLLAMA: backend})
    s, body, by = LLMWording(llm).word_reminder(_reminder())
    prompt = "\n".join(m["content"] for m in backend.calls[0][1])
    assert "cafe.aurora@example.test" not in prompt
    assert "$4,800.00" in prompt
    assert by == GeneratedBy.LLM and s == "S INV-1043"


def test_remote_wording_masks_names(events: Any, stub: Any) -> None:
    """T-AGT-02 proves AGT-16, CTL-AI-03: remote wording sees placeholders, result unmasked."""

    def respond(req: Any, messages: list[dict[str, str]]) -> str:
        text = "\n".join(m["content"] for m in messages)
        assert "Golden Mills" not in text and "Tiny Bakery" not in text
        return '{"subject": "Bill GM-2201", "body": "Hello {SUPPLIER_1}, regards {BUSINESS}"}'

    backend = stub(LLMBackend.OPENAI, remote=True, responder=respond)
    llm = LLM(mode="openai", events=events, backends={LLMBackend.OPENAI: backend})
    s, body, by = LLMWording(llm).word_delay_request(_delay())
    assert body == "Hello Golden Mills, regards Tiny Bakery"
    assert by == GeneratedBy.LLM
