"""Explanations from code facts, with a post-check (CTL-AI-01): T-AGT-09."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from agent import explain
from agent.llm import LLM
from contracts.dto_financing import FinancingComparisonDTO, FinancingRequest
from contracts.dto_planning import ForecastDTO
from contracts.enums import EventType, LLMBackend
from contracts.testing.builders import make_forecast_inputs_tiny
from contracts.testing.fakes import FakeFinancing, FakeForecast


@pytest.fixture
def crunch() -> ForecastDTO:
    return FakeForecast().forecast(make_forecast_inputs_tiny(crunch=True))


@pytest.fixture
def calm() -> ForecastDTO:
    return FakeForecast().forecast(make_forecast_inputs_tiny())


@pytest.fixture
def comparison() -> FinancingComparisonDTO:
    req = FinancingRequest(
        amount_cents=300000,
        term_days=30,
        as_of=date(2026, 10, 14),
        avg_daily_card_sales_cents=50000,
        eligible_invoices=[],
        supplier_terms_available=True,
    )
    return FakeFinancing().compare(req)


def _ollama(stub: Any, events: Any, answer: str) -> LLM:
    backend = stub(LLMBackend.OLLAMA, [answer], model="phi3:mini")
    return LLM(mode="ollama", events=events, backends={LLMBackend.OLLAMA: backend})


def test_postcheck_rejects_numbers_not_in_facts() -> None:
    """T-AGT-09 proves AGT-10, CTL-AI-01: amounts, percentages and other numbers must come
    from the facts."""
    facts = {"amount": "$4,800.00", "week": "3", "rate": "18.00%"}
    assert explain.postcheck("Pay $4,800.00 by week 3 at 18.00%.", facts)
    assert not explain.postcheck("Pay $4,900.00 by week 3.", facts)
    assert not explain.postcheck("Pay $4,800.00 by week 4.", facts)
    assert not explain.postcheck("Rate 19.00%.", facts)
    assert not explain.postcheck("Pay 4800 now.", facts)


def test_mock_forecast_explanation_uses_facts(crunch: ForecastDTO, events: Any) -> None:
    """T-AGT-09 proves PLN-03, AGT-10: the template sentence is filled from drivers.facts."""
    text = explain.explain_forecast(LLM(mode="mock", events=events), crunch, events)
    f = crunch.drivers.facts
    assert text == (
        f"Cash dips below zero in week {f['week']} (from {f['week_start']}) because "
        f"{f['main_outflow']} ({f['main_outflow_amount']}) is due before "
        f"{f['delayed_count']} customer invoices ({f['delayed_total']}) are expected to be paid."
    )


def test_mock_forecast_no_crossing(calm: ForecastDTO, events: Any) -> None:
    """T-AGT-09 proves AGT-10: no crossing → the stays-positive sentence."""
    text = explain.explain_forecast(LLM(mode="mock", events=events), calm, events)
    assert text == "Cash stays above zero in all 13 weeks of the base case."


def test_llm_text_with_invented_number_falls_back(
    crunch: ForecastDTO, events: Any, stub: Any
) -> None:
    """T-AGT-09 proves AGT-10, CTL-AI-01: an LLM explanation with a number not in the facts
    is replaced by the template and the fallback is logged."""
    llm = _ollama(
        stub, events, '{"text": "Cash goes negative in week 2 because of $9,999.00 of bills."}'
    )
    text = explain.explain_forecast(llm, crunch, events)
    assert "$9,999.00" not in text
    assert text.startswith("Cash dips below zero in week 2")
    fb = events.of(EventType.LLM_FALLBACK_USED)
    assert fb[-1] == {
        "task": "EXPLAIN_FORECAST",
        "from": "OLLAMA",
        "to": "TEMPLATE",
        "reason": "postcheck",
    }


def test_llm_text_with_fact_numbers_is_kept(crunch: ForecastDTO, events: Any, stub: Any) -> None:
    """T-AGT-09 proves AGT-10: an LLM explanation that uses only fact numbers is kept."""
    llm = _ollama(
        stub,
        events,
        '{"text": "In week 2, $5,000.00 of supplier bills land before $600.00 arrives."}',
    )
    text = explain.explain_forecast(llm, crunch, events)
    assert text == "In week 2, $5,000.00 of supplier bills land before $600.00 arrives."
    assert events.of(EventType.LLM_FALLBACK_USED) == []


def test_financing_facts_are_preformatted_strings(comparison: FinancingComparisonDTO) -> None:
    """T-AGT-09 proves AGT-10: financing facts are strings computed by code."""
    facts = explain.financing_facts(comparison)
    assert all(isinstance(v, str) for v in facts.values())
    assert facts["amount"] == "$3,000.00"
    assert facts["cheapest_name"] and facts["dearest_name"]
    assert facts["dearest_ear"].endswith("%")


def test_mock_financing_explanation(comparison: FinancingComparisonDTO, events: Any) -> None:
    """T-AGT-09 proves AGT-10: the financing template names cheapest and dearest with rates."""
    text = explain.explain_financing(LLM(mode="mock", events=events), comparison, events)
    facts = explain.financing_facts(comparison)
    assert facts["cheapest_name"] in text and facts["dearest_name"] in text
    assert facts["dearest_ear"] in text
    assert explain.postcheck(text, facts)
    assert "not financial advice" in text


def test_financing_no_eligible(events: Any) -> None:
    """T-AGT-09 proves AGT-10: no eligible option → the fixed no-options sentence."""
    empty = FinancingComparisonDTO(
        request=FinancingRequest(
            amount_cents=100,
            term_days=1,
            as_of=date(2026, 10, 14),
            avg_daily_card_sales_cents=0,
            eligible_invoices=[],
            supplier_terms_available=False,
        ),
        options=[],
        cheapest_id=None,
        most_expensive_id=None,
        catalogue_as_of=date(2026, 10, 14),
        catalogue_source="test",
        notices=[],
    )
    text = explain.explain_financing(LLM(mode="mock", events=events), empty, events)
    assert text == "No eligible financing options for this request."
