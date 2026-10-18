"""LLM and router DTOs (CON-15)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from contracts._model import Frozen
from contracts.enums import DataClass, Intent, LLMBackend, LLMTask
from contracts.untrusted import Untrusted


class LLMRequest(Frozen):
    task: LLMTask
    payload: dict[str, Any]
    data_class: DataClass
    output_schema: dict[str, Any]
    untrusted_blocks: list[Untrusted] = []


class LLMResponse(Frozen):
    task: LLMTask
    backend: LLMBackend
    model: str
    output: dict[str, Any]
    cached: bool
    fallback_used: bool
    latency_ms: int
    attempts: list[LLMBackend] = []  # backends tried in order (hybrid routing)


class RouterResult(Frozen):
    intent: Intent
    confidence: Decimal
    method: Literal["rule", "llm", "fallback"]
    params: dict[str, Any]
    missing: list[str]
