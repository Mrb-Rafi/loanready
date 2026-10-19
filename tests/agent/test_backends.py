"""Ollama and OpenAI backends against stub clients (PRD/09 §2.1); live checks are optional.

T-AGT-01 (remote policy before any network call), T-AGT-04 (backend errors fall back).
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from agent.backends import BackendError, strict_schema
from agent.backends.ollama_backend import OllamaBackend
from agent.backends.openai_backend import OpenAIBackend
from agent.budget import Budget
from agent.prompts import EXPLAIN_SCHEMA, WORDING_SCHEMA, classify_schema
from contracts.dto_agent import LLMRequest
from contracts.enums import DataClass, LLMTask
from contracts.errors import RemoteNotAllowed

MSGS = [{"role": "user", "content": "hello"}]


def _req(task: LLMTask, dc: DataClass, schema: dict[str, Any]) -> LLMRequest:
    return LLMRequest(task=task, payload={}, data_class=dc, output_schema=schema)


class OllamaStub:
    def __init__(self, reply: Any = None, exc: Exception | None = None) -> None:
        self.reply, self.exc, self.kwargs = reply, exc, {}

    def chat(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return SimpleNamespace(message=SimpleNamespace(content=self.reply))


def test_ollama_request_shape() -> None:
    """T-AGT-04 proves §2.1: schema as format, temperature 0, seed, 2,048 context, a small
    per-task output allowance and a 10-minute keep-alive (sized for this laptop)."""
    client = OllamaStub(json.dumps({"label": "5000", "confidence": 0.7}))
    b = OllamaBackend("http://127.0.0.1:11434", "phi3:mini", client=client)
    schema = classify_schema(["5000", "6900"])
    out = b.generate(_req(LLMTask.CATEGORISE_TXN, DataClass.C2_COUNTERPARTY, schema), MSGS)
    assert json.loads(out)["label"] == "5000"
    kw = client.kwargs
    assert kw["format"] == schema and kw["keep_alive"] == "10m"
    assert kw["options"] == {"temperature": 0, "seed": 42, "num_ctx": 2048, "num_predict": 60}


def test_ollama_errors_map_to_backend_errors() -> None:
    """T-AGT-04 proves AGT-20: timeouts and connection errors become fallback reasons."""
    import httpx

    for exc, reason in (
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("down"), "connection"),
    ):
        b = OllamaBackend("http://x", "m", client=OllamaStub(exc=exc))
        with pytest.raises(BackendError) as info:
            b.generate(_req(LLMTask.EXPLAIN_FORECAST, DataClass.C1_AGGREGATE, EXPLAIN_SCHEMA), MSGS)
        assert info.value.reason == reason


class OpenAIStub:
    """``client.chat.completions.create(**kw)`` with a scripted list of results/exceptions."""

    def __init__(self, script: list[Any]) -> None:
        self.script, self.calls = list(script), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _completion(content: str, prompt: int = 50, completion: int = 20) -> Any:
    msg = SimpleNamespace(content=content, refusal=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
    )


def _budget() -> Budget:
    return Budget(
        max_calls_per_day=10,
        max_usd_per_day=Decimal("1"),
        price_in_per_mtok=Decimal("1"),
        price_out_per_mtok=Decimal("2"),
    )


def test_openai_strict_schema_and_usage_booked() -> None:
    """T-AGT-06 proves AGT-13, §2.1: structured output with a strict schema, the task's
    output allowance, and the real token usage booked in the budget."""
    client = OpenAIStub([_completion(json.dumps({"subject": "s", "body": "b"}), 100, 40)])
    budget = _budget()
    b = OpenAIBackend("sk-test-key-not-real", "small-1", budget, client=client)
    out = b.generate(_req(LLMTask.DRAFT_REMINDER, DataClass.C2_COUNTERPARTY, WORDING_SCHEMA), MSGS)
    assert json.loads(out) == {"subject": "s", "body": "b"}
    kw = client.calls[0]
    fmt = kw["response_format"]["json_schema"]
    assert fmt["strict"] is True and fmt["schema"]["additionalProperties"] is False
    assert fmt["schema"]["required"] == ["subject", "body"]
    assert "maxLength" not in json.dumps(fmt["schema"])
    assert kw["max_completion_tokens"] == 420 and kw["temperature"] == 0
    assert budget.usage().calls == 1 and budget.usage().usd_micro == 100 + 80


def test_openai_drops_unsupported_parameter() -> None:
    """T-AGT-04 proves §2.1: a model that rejects temperature is retried without it."""
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    bad = openai.BadRequestError(
        "Unsupported parameter: 'temperature'",
        response=httpx.Response(400, request=request),
        body=None,
    )
    client = OpenAIStub([bad, _completion(json.dumps({"text": "ok"}))])
    b = OpenAIBackend("sk-test-key-not-real", "small-1", _budget(), client=client)
    b.generate(_req(LLMTask.EXPLAIN_FORECAST, DataClass.C1_AGGREGATE, EXPLAIN_SCHEMA), MSGS)
    assert "temperature" in client.calls[0] and "temperature" not in client.calls[1]


@pytest.mark.parametrize(
    ("task", "dc"),
    [
        (LLMTask.EXPLAIN_FORECAST, DataClass.C3_BANK_ID),
        (LLMTask.CATEGORISE_TXN, DataClass.C4_DOCUMENT),
        (LLMTask.TIDY_RECEIPT_TEXT, DataClass.C2_COUNTERPARTY),
    ],
)
def test_openai_backend_refuses_before_network(task: LLMTask, dc: DataClass) -> None:
    """T-AGT-01 proves AGT-01, CTL-AI-02: the backend's own check raises RemoteNotAllowed
    before any network call or budget use, even if handed a forbidden request directly."""
    client = OpenAIStub([])
    budget = _budget()
    b = OpenAIBackend("sk-test-key-not-real", "small-1", budget, client=client)
    with pytest.raises(RemoteNotAllowed):
        b.generate(_req(task, dc, EXPLAIN_SCHEMA), MSGS)
    assert client.calls == [] and budget.usage().calls == 0


def test_key_never_in_repr() -> None:
    """T-AGT-01 proves AGT-14: the API key is not shown in the backend's repr."""
    b = OpenAIBackend("sk-secret-value-123456", "small-1", _budget())
    assert "sk-secret" not in repr(b)


def test_strict_schema_requires_every_property() -> None:
    """T-AGT-04 proves AGT-08: the strict subset keeps enums, requires all properties."""
    s = strict_schema(classify_schema(["5000", "PERSONAL"]))
    assert s["required"] == ["label", "confidence"] and s["additionalProperties"] is False
    assert s["properties"]["label"]["enum"] == ["5000", "PERSONAL"]


@pytest.mark.optional
def test_live_ollama_classifies() -> None:
    """Optional live check (PRD/09 §11): the local model returns valid JSON for a label."""
    from agent.health import ollama_check

    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    model = os.environ.get("OLLAMA_MODEL", "phi3:mini")
    if not ollama_check(host, model)():
        pytest.skip("Ollama or the model is not available")
    from agent.llm import validate_output

    schema = classify_schema(["5000", "6000", "PERSONAL", "6900"])
    b = OllamaBackend(host, model, timeout_s=180)
    raw = b.generate(_req(LLMTask.CATEGORISE_TXN, DataClass.C2_COUNTERPARTY, schema), MSGS)
    assert validate_output(raw, schema)["label"] in schema["properties"]["label"]["enum"]
