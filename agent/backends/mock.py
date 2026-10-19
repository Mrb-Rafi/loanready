"""Deterministic mock backend (PRD/09 §2.1): no network, always available.

Classification by keyword table (0.60 on a match, 0.0 otherwise, so the owner confirms);
wording and explanations are templates filled from the request facts; intent is UNKNOWN.
"""

from __future__ import annotations

import json
from typing import Any

from agent import prompts
from contracts.dto_agent import LLMRequest
from contracts.enums import Intent, LLMBackend, LLMTask
from contracts.untrusted import Untrusted

# Merchant keyword → label (upper-case substring match on the untrusted text).
KEYWORDS: tuple[tuple[str, str], ...] = (
    ("FLOUR", "5000"),
    ("GRAIN", "5000"),
    ("MILLS", "5000"),
    ("RENT", "6000"),
    ("PAYROLL", "6100"),
    ("POWER", "6200"),
    ("INTERNET", "6200"),
    ("PROMO", "6300"),
    ("FEE", "6400"),
    ("FRESHMART", "PERSONAL"),
    ("CINEPLEX", "PERSONAL"),
    ("TOPUP", "PERSONAL"),
)
MATCH_CONFIDENCE = 0.60
FALLBACK_LABEL = "6900"


def _candidates(req: LLMRequest) -> list[str]:
    enum = req.output_schema.get("properties", {}).get("label", {}).get("enum", [])
    return [str(c) for c in enum]


def _untrusted_text(req: LLMRequest) -> str:
    parts = [str(u) for u in req.untrusted_blocks]
    parts.extend(str(v) for v in req.payload.values() if isinstance(v, Untrusted))
    return " ".join(parts)


def _facts(req: LLMRequest) -> dict[str, str]:
    return {k: str(v) for k, v in req.payload.items() if not isinstance(v, Untrusted)}


def _classify(req: LLMRequest) -> dict[str, Any]:
    candidates = _candidates(req)
    upper = _untrusted_text(req).upper()
    for word, label in KEYWORDS:
        if word in upper and label in candidates:
            return {"label": label, "confidence": MATCH_CONFIDENCE}
    if req.task == LLMTask.PERSONAL_VS_BUSINESS:
        default = next((c for c in candidates if c != "PERSONAL"), candidates[0])
    else:
        default = FALLBACK_LABEL if FALLBACK_LABEL in candidates else candidates[0]
    return {"label": default, "confidence": 0}


def _reminder(req: LLMRequest) -> dict[str, Any]:
    facts = _facts(req)
    subject, body = prompts.REMINDER_TEMPLATES.get(
        facts.get("tone", "FRIENDLY"), prompts.REMINDER_TEMPLATES["FRIENDLY"]
    )
    return {"subject": prompts.render(subject, facts), "body": prompts.render(body, facts)}


def _delay(req: LLMRequest) -> dict[str, Any]:
    facts = _facts(req)
    subject, body = prompts.DELAY_TEMPLATE
    return {"subject": prompts.render(subject, facts), "body": prompts.render(body, facts)}


def _explain_forecast(req: LLMRequest) -> dict[str, Any]:
    facts = _facts(req)
    if facts.get("crossing") != "yes":
        return {"text": prompts.FORECAST_POSITIVE_TEXT}
    return {"text": prompts.render(prompts.FORECAST_CROSSING_TEMPLATE, facts)}


def _explain_financing(req: LLMRequest) -> dict[str, Any]:
    facts = _facts(req)
    if facts.get("has_options") != "yes":
        return {"text": prompts.FINANCING_NONE_TEXT}
    return {"text": prompts.render(prompts.FINANCING_TEMPLATE, facts)}


def _tidy(req: LLMRequest) -> dict[str, Any]:
    return {"text": " ".join(_untrusted_text(req).split())[:600]}


def _intent(_req: LLMRequest) -> dict[str, Any]:
    return {"intent": Intent.UNKNOWN.value, "confidence": 0}


_HANDLERS = {
    LLMTask.CATEGORISE_TXN: _classify,
    LLMTask.PERSONAL_VS_BUSINESS: _classify,
    LLMTask.DRAFT_REMINDER: _reminder,
    LLMTask.DRAFT_DELAY_REQUEST: _delay,
    LLMTask.EXPLAIN_FORECAST: _explain_forecast,
    LLMTask.EXPLAIN_FINANCING: _explain_financing,
    LLMTask.TIDY_RECEIPT_TEXT: _tidy,
    LLMTask.INTENT_PICK: _intent,
}


class MockBackend:
    """Answers every task deterministically from the request (messages are ignored)."""

    name = LLMBackend.MOCK
    model = "mock"
    remote = False

    def generate(self, req: LLMRequest, messages: list[dict[str, str]]) -> str:
        return json.dumps(_HANDLERS[req.task](req), ensure_ascii=False, sort_keys=True)
