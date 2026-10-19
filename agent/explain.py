"""Explanations of figures computed by code (PRD/09 §9, AGT-10, CTL-AI-01, REG-AI-03 LLM09).

Facts are preformatted strings from code; the model only words them. ``postcheck`` rejects
any output number that is not in the facts, and the template is used instead. The UI labels
LLM text "AI-generated explanation of figures calculated by LoanReady" (REG-AI-04).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from agent import prompts
from contracts.dto_agent import LLMRequest
from contracts.dto_financing import FinancingComparisonDTO
from contracts.dto_planning import ForecastDTO
from contracts.enums import DataClass, EventType, LLMBackend, LLMTask
from contracts.money import fmt, pct
from contracts.services import LLMClient

EventSink = Callable[[EventType, dict[str, Any]], None]

# Thousands-grouped or plain numbers, with optional decimals ("4,800.00", "2026", "18.00").
_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return set(_NUMBER_RE.findall(text))


def postcheck(text: str, facts: dict[str, str]) -> bool:
    """True if every number in ``text`` (currency amounts, percentages, counts, weeks,
    dates) appears as a number in one of the fact strings (AGT-10)."""
    allowed: set[str] = set()
    for value in facts.values():
        allowed |= _numbers(str(value))
    return _numbers(text) <= allowed


def forecast_facts(forecast: ForecastDTO) -> dict[str, str]:
    """Facts for the forecast sentence: the base-case drivers' preformatted facts (PLN-03)."""
    facts = {k: str(v) for k, v in forecast.drivers.facts.items()}
    facts["crossing"] = "yes" if forecast.drivers.crossing_week is not None else "no"
    facts["horizon_weeks"] = "13"
    return facts


def financing_facts(comparison: FinancingComparisonDTO) -> dict[str, str]:
    """Facts for the financing sentence, all formatted by code (money via ``fmt``, rates via
    ``pct``). EAR = effective annual rate of the option's cash flows, computed by WP5."""
    by_id = {o.product_id: o for o in comparison.options}
    cheap = by_id.get(comparison.cheapest_id or "")
    dear = by_id.get(comparison.most_expensive_id or "")
    eligible = [o for o in comparison.options if o.eligible]
    facts = {
        "amount": fmt(comparison.request.amount_cents),
        "term_days": str(comparison.request.term_days),
        "eligible_count": str(len(eligible)),
        "option_count": str(len(comparison.options)),
        "has_options": "yes" if cheap and dear else "no",
    }
    if cheap and dear:
        facts.update(
            {
                "cheapest_name": cheap.name,
                "cheapest_ear": pct(cheap.ear) if cheap.ear is not None else "n/a",
                "cheapest_cost": fmt(cheap.total_cost_cents),
                "dearest_name": dear.name,
                "dearest_ear": pct(dear.ear) if dear.ear is not None else "n/a",
                "dearest_cost": fmt(dear.total_cost_cents),
            }
        )
    return facts


def _template_text(task: LLMTask, facts: dict[str, str]) -> str:
    if task == LLMTask.EXPLAIN_FORECAST:
        if facts.get("crossing") != "yes":
            return prompts.FORECAST_POSITIVE_TEXT
        return prompts.render(prompts.FORECAST_CROSSING_TEMPLATE, facts)
    if facts.get("has_options") != "yes":
        return prompts.FINANCING_NONE_TEXT
    return prompts.render(prompts.FINANCING_TEMPLATE, facts)


def _explain(llm: LLMClient, task: LLMTask, facts: dict[str, str], events: EventSink | None) -> str:
    resp = llm.complete(
        LLMRequest(
            task=task,
            payload=dict(facts),
            data_class=DataClass.C1_AGGREGATE,
            output_schema=prompts.EXPLAIN_SCHEMA,
        )
    )
    text = str(resp.output.get("text", "")).strip()
    if resp.backend == LLMBackend.MOCK:
        return text or _template_text(task, facts)
    if text and postcheck(text, facts):
        return text
    if events is not None:
        events(
            EventType.LLM_FALLBACK_USED,
            {
                "task": task.value,
                "from": resp.backend.value,
                "to": "TEMPLATE",
                "reason": "postcheck",
            },
        )
    return _template_text(task, facts)


def explain_forecast(llm: LLMClient, forecast: ForecastDTO, events: EventSink | None = None) -> str:
    return _explain(llm, LLMTask.EXPLAIN_FORECAST, forecast_facts(forecast), events)


def explain_financing(
    llm: LLMClient, comparison: FinancingComparisonDTO, events: EventSink | None = None
) -> str:
    return _explain(llm, LLMTask.EXPLAIN_FINANCING, financing_facts(comparison), events)
