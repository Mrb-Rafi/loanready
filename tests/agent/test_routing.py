"""Hybrid routing and fallback across backends (PRD/09 §2.2, AGT-19..21): T-AGT-11, T-AGT-12."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent import routing
from agent.cache import LLMCache
from agent.health import Health
from agent.llm import LLM, BackendError
from agent.prompts import EXPLAIN_SCHEMA
from contracts.dto_agent import LLMRequest
from contracts.enums import DataClass, EventType, LLMBackend, LLMTask

OL, R, M = LLMBackend.OLLAMA, LLMBackend.OPENAI, LLMBackend.MOCK
GOOD = json.dumps({"text": "Cash stays positive."})


def _all(_name: LLMBackend) -> bool:
    return True


def _req(task: LLMTask = LLMTask.EXPLAIN_FORECAST, dc: DataClass = DataClass.C1_AGGREGATE) -> Any:
    return LLMRequest(
        task=task, payload={"crossing": "no"}, data_class=dc, output_schema=EXPLAIN_SCHEMA
    )


def test_default_routes_per_task() -> None:
    """T-AGT-11 proves AGT-19: wording and explanations try OpenAI first, categorisation the
    local model first, receipt text and intents stay local; mock is always last."""
    r = routing.resolve
    assert r("hybrid", LLMTask.DRAFT_REMINDER, DataClass.C2_COUNTERPARTY, _all) == [R, OL, M]
    assert r("hybrid", LLMTask.EXPLAIN_FINANCING, DataClass.C1_AGGREGATE, _all) == [R, OL, M]
    assert r("hybrid", LLMTask.CATEGORISE_TXN, DataClass.C2_COUNTERPARTY, _all) == [OL, R, M]
    assert r("hybrid", LLMTask.TIDY_RECEIPT_TEXT, DataClass.C4_DOCUMENT, _all) == [OL, M]
    assert r("hybrid", LLMTask.INTENT_PICK, DataClass.C2_COUNTERPARTY, _all) == [OL, M]
    assert r("mock", LLMTask.DRAFT_REMINDER, DataClass.C2_COUNTERPARTY, _all) == [M]
    assert r("ollama", LLMTask.DRAFT_REMINDER, DataClass.C2_COUNTERPARTY, _all) == [OL, M]
    # openai mode: local-only tasks get mock only
    assert r("openai", LLMTask.INTENT_PICK, DataClass.C2_COUNTERPARTY, _all) == [M]


def test_forbidden_remote_removed_even_when_overridden(tmp_path: Path) -> None:
    """T-AGT-11 proves AGT-19, CTL-AI-02: an override listing OpenAI for document text and for
    a C4 categorisation request cannot send it out."""
    f = tmp_path / "routes.json"
    f.write_text(json.dumps({"TIDY_RECEIPT_TEXT": ["OPENAI", "OLLAMA"]}), encoding="utf-8")
    over = routing.load_overrides(f)
    assert routing.resolve(
        "hybrid", LLMTask.TIDY_RECEIPT_TEXT, DataClass.C4_DOCUMENT, _all, over
    ) == [OL, M]
    assert routing.resolve("hybrid", LLMTask.CATEGORISE_TXN, DataClass.C4_DOCUMENT, _all) == [OL, M]


def test_override_file_validated(tmp_path: Path) -> None:
    """T-AGT-11 proves AGT-19: unknown task or backend names in LLM_ROUTES_FILE are refused."""
    f = tmp_path / "routes.json"
    f.write_text(json.dumps({"DRAFT_REMINDER": ["CLAUDE"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        routing.load_overrides(f)
    f.write_text(json.dumps({"NOT_A_TASK": ["OLLAMA"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        routing.load_overrides(f)


def test_unavailable_backends_skipped() -> None:
    """T-AGT-11 proves AGT-19, §2.3: an unavailable backend is not tried."""
    up = {OL: False, R: True}
    chain = routing.resolve(
        "hybrid", LLMTask.DRAFT_REMINDER, DataClass.C2_COUNTERPARTY, lambda n: up[n]
    )
    assert chain == [R, M]


def test_fallback_chain_events_and_attempts(events: Any, stub: Any) -> None:
    """T-AGT-12 proves AGT-20: OpenAI times out, Ollama answers; one LLM_FALLBACK_USED with
    from/to/reason, attempts recorded, the answering backend reported."""
    remote = stub(R, [BackendError("timeout")], remote=True)
    local = stub(OL, [GOOD])
    llm = LLM(mode="hybrid", events=events, backends={R: remote, OL: local})
    resp = llm.complete(_req())
    assert resp.backend == OL and resp.attempts == [R, OL] and resp.fallback_used
    fb = events.of(EventType.LLM_FALLBACK_USED)
    assert fb == [
        {"task": "EXPLAIN_FORECAST", "from": "OPENAI", "to": "OLLAMA", "reason": "timeout"}
    ]


def test_circuit_breaker_marks_backend_off() -> None:
    """T-AGT-12 proves §2.3: three failures in a row switch a backend off for 5 minutes; a
    success resets the count; availability is cached for 60 s."""
    t = [0.0]
    calls = [0]

    def check() -> bool:
        calls[0] += 1
        return True

    h = Health({OL: check}, now=lambda: t[0])
    assert h.available(OL) and h.available(OL) and calls[0] == 1  # cached
    h.record_failure(OL)
    h.record_failure(OL)
    h.record_success(OL)
    h.record_failure(OL)
    assert h.available(OL)  # reset by the success
    h.record_failure(OL)
    h.record_failure(OL)
    assert not h.available(OL)
    t[0] += 299
    assert not h.available(OL)
    t[0] += 2
    assert h.available(OL)
    assert h.available(M) and not h.available(R)  # mock always; unconfigured never


def test_breaker_trips_through_llm(events: Any, stub: Any) -> None:
    """T-AGT-12 proves AGT-20, §2.3: after three failed calls the local model is skipped."""
    local = stub(OL, [BackendError("connection")] * 3)
    llm = LLM(mode="ollama", events=events, backends={OL: local}, health=Health({OL: lambda: True}))
    for _ in range(3):
        assert llm.complete(_req()).backend == M
    assert llm.complete(_req()).attempts == [M]
    assert len(local.calls) == 3


def test_cache_hit_on_earlier_backend_wins(events: Any, stub: Any) -> None:
    """T-AGT-05 proves AGT-12, AGT-21: a cached OpenAI answer is served without calling any
    backend or using budget; a different backend never gets another's cached answer."""
    cache = LLMCache()
    remote = stub(R, [GOOD], remote=True, model="small-1")
    local = stub(OL, [], fail_if_called=True)
    llm = LLM(mode="hybrid", events=events, backends={R: remote, OL: local}, cache=cache)
    first = llm.complete(_req())
    assert first.backend == R and not first.cached
    remote.fail_if_called = True
    second = llm.complete(_req())
    assert second.cached and second.backend == R and second.output == first.output
    calls = events.of(EventType.LLM_REMOTE_CALL)
    assert [c["cached"] for c in calls] == [False, True]
    only_local = LLM(mode="ollama", events=events, backends={OL: stub(OL, [GOOD])}, cache=cache)
    assert not only_local.complete(_req()).cached


def test_status_reports_routes_and_budget(stub: Any) -> None:
    """T-AGT-11 proves §2.3, UI-17: status lists per-task routes and the remote budget."""
    from decimal import Decimal

    from agent.budget import Budget

    remote = stub(R, [], remote=True)
    remote.budget = Budget(max_calls_per_day=5, max_usd_per_day=Decimal("1"))
    llm = LLM(mode="hybrid", backends={R: remote, OL: stub(OL, [])})
    st = llm.status()
    assert st["routes"]["DRAFT_REMINDER"] == ["OPENAI", "OLLAMA", "MOCK"]
    assert st["routes"]["TIDY_RECEIPT_TEXT"] == ["OLLAMA", "MOCK"]
    assert st["remote_ai"] is True
    assert st["remote_budget"]["max_calls_per_day"] == 5
