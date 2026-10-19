"""Remote call and cost guard (PRD/09 §6, AGT-13, CTL-AI-07): T-AGT-06."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from agent.backends.openai_backend import OpenAIBackend
from agent.budget import Budget, BudgetExhausted, to_micro
from agent.cache import LLMCache, cache_key
from agent.llm import LLM
from agent.prompts import EXPLAIN_SCHEMA
from contracts.dto_agent import LLMRequest
from contracts.enums import DataClass, EventType, LLMBackend, LLMTask


def _req() -> LLMRequest:
    return LLMRequest(
        task=LLMTask.EXPLAIN_FORECAST,
        payload={"crossing": "no"},
        data_class=DataClass.C1_AGGREGATE,
        output_schema=EXPLAIN_SCHEMA,
    )


def test_cost_formula_rounds_up_in_micro_dollars() -> None:
    """T-AGT-06 proves AGT-13: usd = in x price_in / 1e6 + out x price_out / 1e6, rounded up
    to the micro-dollar (never under-counts), no floats."""
    b = Budget(
        max_calls_per_day=10,
        max_usd_per_day=Decimal("1"),
        price_in_per_mtok=Decimal("0.25"),
        price_out_per_mtok=Decimal("1.25"),
    )
    assert b.cost_micro(1_000_000, 0) == 250_000
    assert b.cost_micro(1000, 400) == 250 + 500
    assert b.cost_micro(1, 0) == 1  # 0.25 micro-dollar rounds up
    assert to_micro(Decimal("4.50")) == 4_500_000


def test_daily_call_cap() -> None:
    """T-AGT-06 proves AGT-13: the daily call cap stops the next call."""
    b = Budget(max_calls_per_day=2, max_usd_per_day=Decimal(0))
    for _ in range(2):
        b.reserve(10, 10)
        b.record(10, 10)
    with pytest.raises(BudgetExhausted, match="call"):
        b.reserve(10, 10)


def test_total_cap_uses_worst_case_and_persists(tmp_path: Path) -> None:
    """T-AGT-06 proves AGT-13: the all-time cap for prepaid credit refuses a call whose worst
    case would cross it, and the spend survives a restart (stored on disk)."""
    db = tmp_path / "llm_cache.db"
    kw: dict[str, Any] = {
        "max_calls_per_day": 1000,
        "max_usd_per_day": Decimal(0),
        "total_max_usd": Decimal("0.001"),
        "price_in_per_mtok": Decimal("1"),
        "price_out_per_mtok": Decimal("1"),
        "path": db,
    }
    b = Budget(**kw)
    b.reserve(100, 400)  # worst case 500 micro-dollars, fits 1,000
    b.record(300, 300)  # 600 spent
    with pytest.raises(BudgetExhausted, match="all-time"):
        b.reserve(100, 400)  # 600 + 500 > 1,000
    again = Budget(**kw)
    assert again.total().usd_micro == 600 and again.total().calls == 1
    assert again.remaining()["max_usd_total"] == "0.001"


def test_exhausted_budget_falls_back_without_network(events: Any) -> None:
    """T-AGT-06 proves AGT-13, CTL-AI-07: with the budget spent the OpenAI client is never
    called; the call falls back with reason budget."""

    class NoNetwork:
        @property
        def chat(self) -> Any:
            pytest.fail("OpenAI client must not be called")

    budget = Budget(max_calls_per_day=0, max_usd_per_day=Decimal(0))
    remote = OpenAIBackend("sk-test-key-not-real", "small-1", budget, client=NoNetwork())
    llm = LLM(mode="openai", events=events, backends={LLMBackend.OPENAI: remote})
    resp = llm.complete(_req())
    assert resp.backend == LLMBackend.MOCK
    assert events.of(EventType.LLM_FALLBACK_USED)[0]["reason"] == "budget"


def test_cache_persists_on_disk(tmp_path: Path) -> None:
    """T-AGT-05 proves AGT-12: the disk cache returns a stored answer by its key."""
    c = LLMCache(tmp_path / "llm_cache.db")
    key = cache_key("OPENAI", "m", "EXPLAIN_FORECAST", [{"role": "user", "content": "x"}], {})
    assert c.get(key) is None
    c.put(key, "OPENAI", "m", "EXPLAIN_FORECAST", {"text": "ok"})
    assert LLMCache(tmp_path / "llm_cache.db").get(key) == {"text": "ok"}
    c.put(key, "OLLAMA", "m", "CATEGORISE_TXN", {"label": "5000", "confidence": 0.7})
    assert c.get(key) == {"label": "5000", "confidence": 0.7}  # model confidence is a float
    other = cache_key("OLLAMA", "m", "EXPLAIN_FORECAST", [{"role": "user", "content": "x"}], {})
    assert other != key
