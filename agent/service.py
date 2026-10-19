"""``Agent``: the WP7 ``AgentService`` (PRD/09 §1). Built by the orchestrator as
``Agent(llm, events=EventSink(audit))``; it never imports governance and never builds an
``Action``."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from agent import explain
from agent.classifier import LLMClassifier
from agent.wording import LLMWording
from contracts.dto_agent import RouterResult
from contracts.dto_financing import FinancingComparisonDTO
from contracts.dto_planning import ForecastDTO
from contracts.enums import EventType, Intent
from contracts.services import LLMClient
from contracts.untrusted import ScanFlag, Untrusted, scan_untrusted

EventSink = Callable[[EventType, dict[str, Any]], None]


def _no_events(_event: EventType, _payload: dict[str, Any]) -> None:
    return None


class Agent:
    def __init__(self, llm: LLMClient, events: EventSink | None = None) -> None:
        self._llm = llm
        self._events: EventSink = events or _no_events
        self._classifier = LLMClassifier(llm)
        self._wording = LLMWording(llm)

    def classifier(self) -> LLMClassifier:
        return self._classifier

    def wording(self) -> LLMWording:
        return self._wording

    def route(self, message: str) -> RouterResult:
        """The rule-based router is a SHOULD module (PRD/09 §8) not built yet: every message
        is UNKNOWN, so the UI shows its help reply. Never builds an action (AGT-17)."""
        return RouterResult(
            intent=Intent.UNKNOWN, confidence=Decimal("0"), method="fallback", params={}, missing=[]
        )

    def explain_forecast(self, forecast: ForecastDTO) -> str:
        return explain.explain_forecast(self._llm, forecast, self._events)

    def explain_financing(self, comparison: FinancingComparisonDTO) -> str:
        return explain.explain_financing(self._llm, comparison, self._events)

    def scan(self, text: Untrusted) -> list[ScanFlag]:
        """Visibility only (AGT-11): flags instruction-like text and logs one event with
        {source, source_ref, pattern_ids}; the defence is that untrusted text never becomes
        an action."""
        flags = scan_untrusted(text)
        if flags:
            self._events(
                EventType.UNTRUSTED_INSTRUCTION_IGNORED,
                {
                    "source": text.source.value,
                    "source_ref": text.source_ref,
                    "pattern_ids": [f.pattern_id for f in flags],
                },
            )
        return flags

    def status(self) -> dict[str, Any]:
        """Mode, available backends, model names and today's remote call count (§2.3)."""
        status = getattr(self._llm, "status", None)
        if callable(status):
            return dict(status())
        return {
            "mode": "MOCK",
            "available": ["MOCK"],
            "models": {"MOCK": "mock"},
            "remote_calls_today": 0,
            "remote_ai": False,
            "remote_tasks": [],
        }
