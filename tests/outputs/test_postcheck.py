"""T-OUT-02 proves CTL-AI-01, REG-COM-01: a wording that fails the post-check (wrong or
extra amount, missing number or date, URL, account, phone, threats, too long) is replaced
by the template; T-OUT-03: every template passes its own post-check."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from contracts.enums import GeneratedBy, Tone
from contracts.testing.builders import make_bill, make_counterparty
from outputs.facts import delay_facts, reminder_facts
from outputs.postcheck import check_delay, check_reminder
from outputs.templates import TemplateWording

AS_OF = date(2026, 10, 14)
GOOD = (
    "Hello Tiny Catering, invoice INV-1 for $600.00 was due on 09 Oct 2026. "
    "Could you look at it? Thanks, Tiny Bakery"
)


class LLMWording:
    def __init__(self, body: str, subject: str = "Invoice INV-1"):
        self.subject, self.body = subject, body

    def word_reminder(self, facts: Any) -> tuple[str, str, GeneratedBy]:
        return self.subject, self.body, GeneratedBy.LLM

    def word_delay_request(self, facts: Any) -> tuple[str, str, GeneratedBy]:
        return self.subject, self.body, GeneratedBy.LLM


@pytest.fixture
def facts(invoice: Any, customer: Any) -> Any:
    return reminder_facts(invoice, customer, AS_OF, "Tiny Bakery")


def test_good_wording_passes(facts: Any) -> None:
    """T-OUT-02: a correct LLM wording has no problems."""
    assert check_reminder("Invoice INV-1", GOOD, facts) == []


@pytest.mark.parametrize(
    "body",
    [
        GOOD.replace("$600.00", "$650.00"),  # wrong amount
        GOOD + " Total with fees $612.00.",  # extra amount
        GOOD.replace("INV-1", "the invoice"),  # missing invoice number
        GOOD.replace("09 Oct 2026", "2026-10-09"),  # wrong date format
        GOOD + " Pay at https://pay.example.test",  # URL
        GOOD + " Account 4455667788.",  # bank account
        GOOD + " Call +999 555 0101 99.",  # phone
        GOOD + " Otherwise we will go to court.",  # threat
        GOOD + " We may report you to the police.",  # threat
        GOOD + " x" * 700,  # too long
    ],
)
def test_failures_fall_back_to_template(outputs: Any, facts: Any, body: str) -> None:
    """T-OUT-02: each failure -> template text, generated_by TEMPLATE, fallback flagged."""
    assert check_reminder("Payment reminder", body, facts) != []
    d = outputs.draft_reminder(facts, LLMWording(body, subject="Payment reminder"))
    assert d.generated_by == GeneratedBy.TEMPLATE
    assert outputs.last_fallback and outputs.last_problems
    assert d.subject == "Friendly reminder: invoice INV-1"


@pytest.mark.parametrize("days", [3, 10, 30])
def test_every_reminder_template_passes(invoice: Any, customer: Any, days: int) -> None:
    """T-OUT-03: FRIENDLY, FIRM and FINAL templates pass their own post-check."""
    inv = invoice.model_copy(update={"due_date": AS_OF - timedelta(days=days)})
    f = reminder_facts(inv, customer, AS_OF, "Tiny Bakery")
    subject, body, by = TemplateWording().word_reminder(f)
    assert by == GeneratedBy.TEMPLATE
    assert check_reminder(subject, body, f) == []
    assert f.tone in (Tone.FRIENDLY, Tone.FIRM, Tone.FINAL)


def test_delay_template_passes_and_requires_dates() -> None:
    """T-OUT-03: the delay template passes; dropping the proposed date fails."""
    supplier = make_counterparty(id="cpt_tiny_mills", name="Tiny Mills", contact_ref="b@m.test")
    bill = make_bill(late_penalty=False, grace_days=14)
    f = delay_facts(bill, supplier, bill.due_date + timedelta(days=7), "Tiny Bakery")
    subject, body, _ = TemplateWording().word_delay_request(f)
    assert check_delay(subject, body, f) == []
    assert check_delay(subject, body.replace(f.proposed_date_str, "later"), f) != []
