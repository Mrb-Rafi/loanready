"""Output validation, retry and fallback (CTL-AI-05): T-AGT-04."""

from __future__ import annotations

from typing import Any

import pytest

from agent.llm import LLM, BackendError, validate_output
from agent.prompts import EXPLAIN_SCHEMA, RETRY_INSTRUCTION, WORDING_SCHEMA, classify_schema
from contracts.dto_agent import LLMRequest
from contracts.enums import DataClass, EventType, LLMBackend, LLMTask
from contracts.errors import LLMOutputInvalid


def _explain_req() -> LLMRequest:
    return LLMRequest(
        task=LLMTask.EXPLAIN_FORECAST,
        payload={"crossing": "no"},
        data_class=DataClass.C1_AGGREGATE,
        output_schema=EXPLAIN_SCHEMA,
    )


@pytest.mark.parametrize(
    ("raw", "schema"),
    [
        ("not json", EXPLAIN_SCHEMA),
        ('{"text": 5}', EXPLAIN_SCHEMA),
        ('{"text": "' + "x" * 601 + '"}', EXPLAIN_SCHEMA),
        ('{"text": "ok", "extra": 1}', EXPLAIN_SCHEMA),
        ('{"subject": "s"}', WORDING_SCHEMA),
        ('{"label": "9999", "confidence": 0.5}', classify_schema(["5000"])),
        ('{"label": "5000", "confidence": 1.5}', classify_schema(["5000"])),
        ('["5000"]', classify_schema(["5000"])),
    ],
)
def test_invalid_outputs_rejected(raw: str, schema: dict[str, Any]) -> None:
    """T-AGT-04 proves AGT-08, AGT-09: outputs that break the schema are rejected."""
    with pytest.raises(LLMOutputInvalid):
        validate_output(raw, schema)


def test_valid_output_parsed() -> None:
    """T-AGT-04 proves AGT-08: a valid classify answer passes."""
    out = validate_output('{"label": "5000", "confidence": 0.7}', classify_schema(["5000", "6900"]))
    assert out == {"label": "5000", "confidence": 0.7}


def test_invalid_json_retry_then_mock_fallback(events: Any, stub: Any) -> None:
    """T-AGT-04 proves AGT-09, AGT-20, CTL-AI-05: invalid JSON → one retry with
    "Return valid JSON only" → fallback to mock with LLM_FALLBACK_USED."""
    local = stub(LLMBackend.OLLAMA, ["oops", "{still bad"])
    llm = LLM(mode="ollama", events=events, backends={LLMBackend.OLLAMA: local})
    resp = llm.complete(_explain_req())
    assert len(local.calls) == 2
    retry_msgs = local.calls[1][1]
    assert retry_msgs[-1]["content"] == RETRY_INSTRUCTION
    assert resp.backend == LLMBackend.MOCK
    assert resp.fallback_used is True
    assert resp.attempts == [LLMBackend.OLLAMA, LLMBackend.MOCK]
    assert events.of(EventType.LLM_FALLBACK_USED) == [
        {"task": "EXPLAIN_FORECAST", "from": "OLLAMA", "to": "MOCK", "reason": "invalid_json"}
    ]


def test_retry_succeeds_without_fallback(events: Any, stub: Any) -> None:
    """T-AGT-04 proves AGT-09: a valid second answer is used; no fallback event."""
    local = stub(LLMBackend.OLLAMA, ["oops", '{"text": "Fine."}'], model="phi3:mini")
    llm = LLM(mode="ollama", events=events, backends={LLMBackend.OLLAMA: local})
    resp = llm.complete(_explain_req())
    assert resp.backend == LLMBackend.OLLAMA and resp.output == {"text": "Fine."}
    assert resp.fallback_used is False
    assert events.of(EventType.LLM_FALLBACK_USED) == []


def test_backend_error_falls_back_with_reason(events: Any, stub: Any) -> None:
    """T-AGT-12 proves AGT-20: a timeout moves to the next backend with reason 'timeout'
    and records attempts."""
    local = stub(LLMBackend.OLLAMA, [BackendError("timeout", "slow")])
    llm = LLM(mode="ollama", events=events, backends={LLMBackend.OLLAMA: local})
    resp = llm.complete(_explain_req())
    assert resp.backend == LLMBackend.MOCK
    assert resp.attempts == [LLMBackend.OLLAMA, LLMBackend.MOCK]
    assert events.of(EventType.LLM_FALLBACK_USED)[0]["reason"] == "timeout"


def test_mock_mode_ignores_other_backends(events: Any, stub: Any) -> None:
    """T-AGT-04 proves AGT-22, PRD-NF-02: LLM_BACKEND=mock never calls another backend."""
    local = stub(LLMBackend.OLLAMA, fail_if_called=True)
    llm = LLM(mode="mock", events=events, backends={LLMBackend.OLLAMA: local})
    resp = llm.complete(_explain_req())
    assert resp.backend == LLMBackend.MOCK
    assert resp.attempts == [LLMBackend.MOCK]
    assert resp.cached is False and resp.fallback_used is False and resp.latency_ms == 0


def test_mock_is_deterministic(events: Any) -> None:
    """T-AGT-04 proves AGT-22: the mock gives identical output for identical requests."""
    llm = LLM(mode="mock", events=events)
    assert llm.complete(_explain_req()).output == llm.complete(_explain_req()).output


def test_unconfigured_mode_degrades_to_mock(events: Any) -> None:
    """T-AGT-11 proves AGT-19: with no real backend available, every mode ends in mock."""
    for mode in ("ollama", "openai", "hybrid"):
        resp = LLM(mode=mode, events=events).complete(_explain_req())
        assert resp.backend == LLMBackend.MOCK
        assert resp.attempts == [LLMBackend.MOCK]


def test_settings_mode_is_used(settings: Any) -> None:
    """T-AGT-04 proves AGT-22: LLM(settings) reads LLM_BACKEND (mock in tests)."""
    llm = LLM(settings)
    assert llm.mode == "mock"
