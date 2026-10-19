"""``TemplateWording``: deterministic message text per tone (PRD/08 §3, exact templates).

Implements ``contracts.services.WordingProvider``; used when the agent is absent and as
the fallback whenever an LLM wording fails the post-check.
"""

from __future__ import annotations

from contracts.dto_outputs import DelayFacts, ReminderFacts
from contracts.enums import GeneratedBy, Tone

AI_LABEL_TEMPLATE = "Generated from a LoanReady template."
AI_LABEL_LLM = "Drafted with AI assistance by LoanReady; reviewed and approved by {business_name}."

_SUBJECTS = {
    Tone.FRIENDLY: "Friendly reminder: invoice {invoice_number}",
    Tone.FIRM: "Invoice {invoice_number} is overdue",
    Tone.FINAL: "Final reminder: invoice {invoice_number}",
}
_BODIES = {
    Tone.FRIENDLY: (
        "Hello {customer_name},\n\nThis is a friendly reminder that invoice {invoice_number} "
        "for {amount_str} was due on {due_date_str}. If you have already paid, thank you and "
        "please ignore this message.\n\nKind regards,\n{business_name}\n\n{ai_label}"
    ),
    Tone.FIRM: (
        "Hello {customer_name},\n\nInvoice {invoice_number} for {amount_str} was due on "
        "{due_date_str} and is now {days_overdue} days overdue. Please arrange payment this "
        "week or let us know if there is a problem.\n\nThank you,\n{business_name}\n\n"
        "{ai_label}"
    ),
    Tone.FINAL: (
        "Hello {customer_name},\n\nInvoice {invoice_number} for {amount_str} was due on "
        "{due_date_str} and is {days_overdue} days overdue. Please pay within 7 days. If we "
        "cannot resolve this, we may pause future orders until the balance is settled.\n\n"
        "Regards,\n{business_name}\n\n{ai_label}"
    ),
}
_DELAY_SUBJECT = "Request to adjust payment date for {bill_number}"
_DELAY_BODY = (
    "Hello {supplier_name},\n\nThank you for invoice {bill_number} for {amount_str}, due on "
    "{due_date_str}. Could we pay on {proposed_date_str} instead, within your usual grace "
    "period? Please reply to confirm.\n\nKind regards,\n{business_name}\n\n{ai_label}"
)


def ai_label(generated_by: GeneratedBy, business_name: str) -> str:
    """REG-AI-04 label that ends every message body."""
    if generated_by == GeneratedBy.LLM:
        return AI_LABEL_LLM.format(business_name=business_name)
    return AI_LABEL_TEMPLATE


class TemplateWording:
    def word_reminder(self, facts: ReminderFacts) -> tuple[str, str, GeneratedBy]:
        values = facts.model_dump() | {"ai_label": AI_LABEL_TEMPLATE}
        return (
            _SUBJECTS[facts.tone].format(**values),
            _BODIES[facts.tone].format(**values),
            GeneratedBy.TEMPLATE,
        )

    def word_delay_request(self, facts: DelayFacts) -> tuple[str, str, GeneratedBy]:
        values = facts.model_dump() | {"ai_label": AI_LABEL_TEMPLATE}
        return _DELAY_SUBJECT.format(**values), _DELAY_BODY.format(**values), GeneratedBy.TEMPLATE
