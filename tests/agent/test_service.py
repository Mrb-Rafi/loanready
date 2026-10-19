"""Agent service wiring, scanner and status (AGT-11, §2.3): T-AGT-10."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from agent.llm import LLM
from agent.service import Agent
from contracts.enums import EventType, Intent, SourceKind
from contracts.services import AgentService
from contracts.testing.builders import make_forecast_inputs_tiny
from contracts.testing.fakes import FakeForecast
from contracts.untrusted import Untrusted


@pytest.fixture
def agent(events: Any) -> Agent:
    return Agent(LLM(mode="mock", events=events), events=events)


def test_satisfies_protocol(agent: Agent) -> None:
    """T-AGT-10 proves AGT-11: Agent implements contracts.services.AgentService."""
    assert isinstance(agent, AgentService)


def test_scanner_flags_corpus(
    agent: Agent, events: Any, injection_corpus: list[dict[str, Any]]
) -> None:
    """T-AGT-10 proves AGT-11, CTL-AI-04: the scanner flags >= 18 of the expect_flag payloads
    and emits UNTRUSTED_INSTRUCTION_IGNORED with {source, source_ref, pattern_ids} only."""
    expected = [e for e in injection_corpus if e["expect_flag"]]
    assert len(injection_corpus) >= 20
    flagged = 0
    for entry in expected:
        if agent.scan(Untrusted(entry["text"], SourceKind.BANK, entry["id"])):
            flagged += 1
    assert flagged >= min(18, len(expected))
    payloads = events.of(EventType.UNTRUSTED_INSTRUCTION_IGNORED)
    assert len(payloads) == flagged
    for p in payloads:
        assert set(p) == {"source", "source_ref", "pattern_ids"}
        assert p["source"] == "BANK" and p["pattern_ids"]


def test_benign_text_emits_nothing(agent: Agent, events: Any) -> None:
    """T-AGT-10 proves AGT-11: a benign memo is not flagged and emits no event."""
    assert (
        agent.scan(Untrusted("Payment for invoice INV-1043, thanks!", SourceKind.BANK, "t1")) == []
    )
    assert events.events == []


def test_explain_via_service(agent: Agent) -> None:
    """T-AGT-10 proves PLN-03: explain_forecast through the service gives the template text."""
    text = agent.explain_forecast(FakeForecast().forecast(make_forecast_inputs_tiny(crunch=True)))
    assert text.startswith("Cash dips below zero in week 2")


def test_route_without_router_is_unknown(agent: Agent) -> None:
    """T-AGT-10 proves AGT-17: until the SHOULD router lands, route() returns UNKNOWN with
    method 'fallback' and never builds an action."""
    res = agent.route("pay Golden Mills on friday")
    assert res.intent == Intent.UNKNOWN and res.method == "fallback"
    assert res.params == {} and res.missing == []


def test_status_mock(agent: Agent) -> None:
    """T-AGT-10 proves AGT-03, REG-DP-03: status reports mode, backends and remote AI off."""
    st = agent.status()
    assert st["mode"] == "MOCK"
    assert st["available"] == ["MOCK"]
    assert st["models"] == {"MOCK": "mock"}
    assert st["remote_calls_today"] == 0
    assert st["remote_ai"] is False
    assert "EXPLAIN_FORECAST" in st["remote_tasks"]
    assert "TIDY_RECEIPT_TEXT" not in st["remote_tasks"]


def test_status_counts_remote_calls(events: Any, stub: Any) -> None:
    """T-AGT-10 proves AGT-02: remote calls are counted per day in status."""
    from agent.prompts import EXPLAIN_SCHEMA
    from contracts.dto_agent import LLMRequest
    from contracts.enums import DataClass, LLMBackend, LLMTask

    remote = stub(LLMBackend.OPENAI, ['{"text": "ok"}'], remote=True, model="gpt-test")
    llm = LLM(mode="openai", events=events, backends={LLMBackend.OPENAI: remote})
    llm.complete(
        LLMRequest(
            task=LLMTask.EXPLAIN_FORECAST,
            payload={},
            data_class=DataClass.C1_AGGREGATE,
            output_schema=EXPLAIN_SCHEMA,
        )
    )
    st = Agent(llm, events=events).status()
    assert st["remote_calls_today"] == 1
    assert st["remote_ai"] is True
    assert st["models"]["OPENAI"] == "gpt-test"


def test_api_key_never_logged(caplog: pytest.LogCaptureFixture, events: Any, stub: Any) -> None:
    """T-AGT-10 proves AGT-14, CTL-SEC-01: a backend error carrying a key is logged without it."""
    from agent.llm import BackendError
    from agent.prompts import EXPLAIN_SCHEMA
    from contracts.dto_agent import LLMRequest
    from contracts.enums import DataClass, LLMBackend, LLMTask

    secret = "sk-test-SECRET1234567890"
    remote = stub(
        LLMBackend.OPENAI, [BackendError("connection", f"auth failed {secret}")], remote=True
    )
    llm = LLM(mode="openai", events=events, backends={LLMBackend.OPENAI: remote})
    caplog.set_level(logging.DEBUG)
    llm.complete(
        LLMRequest(
            task=LLMTask.EXPLAIN_FORECAST,
            payload={},
            data_class=DataClass.C1_AGGREGATE,
            output_schema=EXPLAIN_SCHEMA,
        )
    )
    assert secret not in caplog.text
    assert all(secret not in repr(p) for _, p in events.events)
