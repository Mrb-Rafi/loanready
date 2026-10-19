"""LLM wording (PRD/09 §7, AGT-16): implements ``contracts.services.WordingProvider``.

The prompt holds only the facts WP6 computed (names masked before any remote call; the
contact ref never enters it). WP6's post-check decides whether the text is used and adds the
AI label (REG-AI-04). Text produced by the mock backend is a template and reported as one.
"""

from __future__ import annotations

from agent.prompts import WORDING_SCHEMA
from contracts.dto_agent import LLMRequest, LLMResponse
from contracts.dto_outputs import DelayFacts, ReminderFacts
from contracts.enums import DataClass, GeneratedBy, LLMBackend, LLMTask
from contracts.services import LLMClient


def _generated_by(resp: LLMResponse) -> GeneratedBy:
    return GeneratedBy.TEMPLATE if resp.backend == LLMBackend.MOCK else GeneratedBy.LLM


class LLMWording:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def _word(self, task: LLMTask, facts: dict[str, str]) -> tuple[str, str, GeneratedBy]:
        resp = self._llm.complete(
            LLMRequest(
                task=task,
                payload=facts,
                data_class=DataClass.C2_COUNTERPARTY,
                output_schema=WORDING_SCHEMA,
            )
        )
        return str(resp.output["subject"]), str(resp.output["body"]), _generated_by(resp)

    def word_reminder(self, facts: ReminderFacts) -> tuple[str, str, GeneratedBy]:
        return self._word(
            LLMTask.DRAFT_REMINDER,
            {
                "invoice_number": facts.invoice_number,
                "customer_name": facts.customer_name,
                "amount_str": facts.amount_str,
                "due_date_str": facts.due_date_str,
                "days_overdue": str(facts.days_overdue),
                "tone": facts.tone.value,
                "channel": facts.channel.value,
                "business_name": facts.business_name,
            },
        )

    def word_delay_request(self, facts: DelayFacts) -> tuple[str, str, GeneratedBy]:
        return self._word(
            LLMTask.DRAFT_DELAY_REQUEST,
            {
                "bill_number": facts.bill_number,
                "supplier_name": facts.supplier_name,
                "amount_str": facts.amount_str,
                "due_date_str": facts.due_date_str,
                "proposed_date_str": facts.proposed_date_str,
                "channel": facts.channel.value,
                "business_name": facts.business_name,
            },
        )
