"""Model backends. Every backend has ``name``, ``model``, ``remote`` and
``generate(req, messages) -> str`` (raw JSON text); see ``agent.llm.Backend``.

Shared here: ``BackendError`` (a backend could not answer; the LLM client falls back to the
next backend) and the per-task output allowance, sized for a 4-core laptop CPU: short labels
for classification and intents, a few hundred tokens for wording and explanations.
"""

from __future__ import annotations

import copy
from typing import Any

from contracts.enums import LLMTask

MAX_OUTPUT_TOKENS: dict[LLMTask, int] = {
    LLMTask.CATEGORISE_TXN: 60,
    LLMTask.PERSONAL_VS_BUSINESS: 60,
    LLMTask.INTENT_PICK: 60,
    LLMTask.EXPLAIN_FORECAST: 260,
    LLMTask.EXPLAIN_FINANCING: 260,
    LLMTask.DRAFT_REMINDER: 420,
    LLMTask.DRAFT_DELAY_REQUEST: 420,
    LLMTask.TIDY_RECEIPT_TEXT: 420,
}


class BackendError(Exception):
    """A backend could not answer. ``reason``: timeout, connection, budget, unavailable,
    refused (the provider rejected the request), policy."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def max_output_tokens(task: LLMTask) -> int:
    return MAX_OUTPUT_TOKENS.get(task, 300)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """The output schema in the subset structured-output APIs accept: an object with every
    property required and no extra properties; length and range keywords are dropped (the
    LLM client still validates them in code, AGT-09)."""
    out = copy.deepcopy(schema)
    out.setdefault("type", "object")
    props: dict[str, Any] = out.get("properties", {})
    for spec in props.values():
        for key in ("maxLength", "minLength", "minimum", "maximum"):
            spec.pop(key, None)
        if "enum" in spec and "type" not in spec:
            spec["type"] = "string"
    out["required"] = list(props)
    out["additionalProperties"] = False
    return out
