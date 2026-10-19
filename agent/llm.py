"""The single entry point for model calls (PRD/09 §2-§6, CTL-AI-02/03/05/07, REG-AI-03).

``LLM.complete(req)``: policy check → prompt build (untrusted only in data blocks) → resolve
the route (``agent.routing``: mode or hybrid per-task route, minus policy-forbidden remote
backends, minus unavailable ones per ``agent.health``) → per backend: cache hit, else remote
guard → mask → call (budget-guarded for OpenAI) → validate (one retry) → unmask → cache; on
any failure move to the next backend, ending in the deterministic mock.

Built from settings (``LLM()``): ``LLM_BACKEND`` picks the mode; Ollama is configured from
``OLLAMA_HOST``/``OLLAMA_MODEL``; OpenAI only when ``OPENAI_API_KEY`` and ``OPENAI_MODEL`` are
set. Tests inject ``backends`` (then every injected backend counts as available).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from agent import policy, routing
from agent.backends import BackendError
from agent.backends.mock import MockBackend
from agent.cache import LLMCache, cache_key
from agent.health import Health
from agent.masking import Masker, assert_no_identifiers
from agent.prompts import RETRY_INSTRUCTION, assert_untrusted_enclosed, build_messages
from contracts.clock import get_clock
from contracts.dto_agent import LLMRequest, LLMResponse
from contracts.enums import CounterpartyKind, DataClass, EventType, LLMBackend, LLMTask
from contracts.errors import LLMOutputInvalid, RemoteNotAllowed

log = logging.getLogger(__name__)

EventSink = Callable[[EventType, dict[str, Any]], None]
Mode = Literal["mock", "ollama", "openai", "hybrid"]


# Payload keys whose values are names to mask before a remote call (AGT-04).
_NAME_KEYS: dict[str, CounterpartyKind] = {
    "customer_name": CounterpartyKind.CUSTOMER,
    "supplier_name": CounterpartyKind.SUPPLIER,
    "counterparty_name": CounterpartyKind.OTHER,
}
_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+|api[_-]?key\S*)", re.IGNORECASE)


class Backend(Protocol):
    name: LLMBackend
    model: str
    remote: bool

    def generate(self, req: LLMRequest, messages: list[dict[str, str]]) -> str: ...


__all__ = ["LLM", "Backend", "BackendError", "build_backends", "validate_output"]

# Fallback reasons that count against a backend's circuit breaker (not policy or budget).
_HEALTH_FAILURES = frozenset({"timeout", "connection", "unavailable", "invalid_json"})


def _no_events(_event: EventType, _payload: dict[str, Any]) -> None:
    return None


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text)


# --- validation (AGT-08, AGT-09) -------------------------------------------------------------


def _field(spec: dict[str, Any]) -> tuple[Any, Any]:
    if "enum" in spec:
        values = tuple(spec["enum"])
        if not values:
            raise LLMOutputInvalid("schema enum is empty")
        return Literal[values], Field(...)  # type: ignore[valid-type]
    kind = spec.get("type")
    if kind == "string":
        return str, Field(..., max_length=spec.get("maxLength"))
    if kind == "number":
        return float, Field(..., ge=spec.get("minimum"), le=spec.get("maximum"))
    if kind == "integer":
        return int, Field(..., ge=spec.get("minimum"), le=spec.get("maximum"))
    raise LLMOutputInvalid(f"unsupported schema type {kind!r}")


def _model_for(schema: dict[str, Any]) -> type[BaseModel]:
    props: dict[str, Any] = schema.get("properties", {})
    fields = {name: _field(spec) for name, spec in props.items()}
    return create_model(  # type: ignore[call-overload,no-any-return]
        "LLMOutput",
        __config__=ConfigDict(extra="forbid", strict=False),
        **fields,
    )


def validate_output(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Parse JSON and validate it against a pydantic model built from the schema; raise
    ``LLMOutputInvalid`` on any problem. Strings stay strings; numbers are not coerced from
    strings."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise LLMOutputInvalid("output is not JSON") from exc
    if not isinstance(data, dict):
        raise LLMOutputInvalid("output is not a JSON object")
    model = _model_for(schema)
    for name, spec in schema.get("properties", {}).items():
        value = data.get(name)
        if spec.get("type") == "string" and not isinstance(value, str):
            raise LLMOutputInvalid(f"{name} must be a string")
        if spec.get("type") == "number" and (
            isinstance(value, bool) or not isinstance(value, int | float)
        ):
            raise LLMOutputInvalid(f"{name} must be a number")
    try:
        return model.model_validate(data).model_dump()
    except ValidationError as exc:
        raise LLMOutputInvalid("output does not match the schema") from exc


# --- client ----------------------------------------------------------------------------------


def build_backends(settings: Any) -> tuple[dict[LLMBackend, Backend], Health]:
    """Real backends configured in ``settings`` plus their health checks."""
    from agent.backends.ollama_backend import OllamaBackend
    from agent.backends.openai_backend import OpenAIBackend
    from agent.budget import Budget
    from agent.cache import default_db_path
    from agent.health import ollama_check, openai_check

    backends: dict[LLMBackend, Backend] = {}
    checks = {}
    if settings.ollama_model:
        backends[LLMBackend.OLLAMA] = OllamaBackend(
            settings.ollama_host, settings.ollama_model, int(settings.ollama_timeout_s)
        )
        checks[LLMBackend.OLLAMA] = ollama_check(settings.ollama_host, settings.ollama_model)
    key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if key and settings.openai_model:
        budget = Budget.from_settings(settings, default_db_path())
        openai_backend = OpenAIBackend(
            key, settings.openai_model, budget, int(settings.openai_timeout_s)
        )
        backends[LLMBackend.OPENAI] = openai_backend
        checks[LLMBackend.OPENAI] = openai_check(openai_backend)
    return backends, Health(checks)


class LLM:
    """``LLMClient`` implementation. ``backends`` injects real or stub backends; the mock is
    always present and always last."""

    def __init__(
        self,
        settings: Any | None = None,
        events: EventSink | None = None,
        *,
        mode: str | None = None,
        backends: Mapping[LLMBackend, Backend] | None = None,
        health: Health | None = None,
        cache: LLMCache | None = None,
        overrides: Mapping[LLMTask, tuple[LLMBackend, ...]] | None = None,
    ) -> None:
        if settings is None and (mode is None or backends is None):
            from infra.settings import get_settings

            settings = get_settings()
        if mode is None:
            mode = str(settings.llm_backend)
        mode = mode.lower()
        if mode not in routing.MODES:
            raise ValueError(f"unknown LLM_BACKEND {mode!r}")
        self.mode: str = mode
        self.events: EventSink = events or _no_events
        self.mock = MockBackend()
        # Tests never reach a real model (CLAUDE.md rule 8): in test mode real backends are
        # only ever injected; optional live tests build them explicitly.
        if backends is None and mode != "mock" and getattr(settings, "env", "") != "test":
            built, built_health = build_backends(settings)
            backends, health = built, health or built_health
            if cache is None:
                from agent.cache import default_db_path

                cache = LLMCache(default_db_path())
        self.backends: dict[LLMBackend, Backend] = {
            k: v for k, v in (backends or {}).items() if k != LLMBackend.MOCK
        }
        # injected backends without a Health count as available (tests and stubs)
        self.health = health or Health({n: (lambda: True) for n in self.backends})
        self.cache = cache
        if overrides is None and settings is not None:
            overrides = routing.load_overrides(getattr(settings, "llm_routes_file", ""))
        self.overrides = dict(overrides or {})
        self._remote_calls: dict[date, int] = {}

    # route ---------------------------------------------------------------------------------

    def resolve(self, task: LLMTask, data_class: DataClass) -> list[Backend]:
        """Backends to try, in order (AGT-19): the mode's route for the task, minus remote
        backends the policy forbids for this task and class, minus backends that are not
        configured or not available, then mock."""
        names = routing.resolve(
            self.mode,
            task,
            data_class,
            lambda n: n in self.backends and self.health.available(n),
            self.overrides,
        )
        return [self.mock if n == LLMBackend.MOCK else self.backends[n] for n in names]

    # call ----------------------------------------------------------------------------------

    def complete(self, req: LLMRequest, *, masker: Masker | None = None) -> LLMResponse:
        policy.check_request(req.task, req.data_class)
        messages = build_messages(req)
        assert_untrusted_enclosed(messages, req)
        chain = self.resolve(req.task, req.data_class)
        attempts: list[LLMBackend] = []
        for i, backend in enumerate(chain):
            attempts.append(backend.name)
            nxt = chain[i + 1].name.value if i + 1 < len(chain) else None
            key = self._key(backend, req, messages)
            hit = self.cache.get(key) if (self.cache is not None and key) else None
            if hit is not None:
                if backend.remote:
                    self._remote_event(req, backend, cached=True)
                return self._response(req, backend, hit, attempts, cached=True, latency=0)
            try:
                output, latency = self._try(backend, req, messages, masker)
            except _Fallback as fb:
                if backend is not self.mock and fb.reason in _HEALTH_FAILURES:
                    self.health.record_failure(backend.name)
                if nxt is None:
                    raise LLMOutputInvalid(f"mock backend failed: {fb.reason}") from None
                self.events(
                    EventType.LLM_FALLBACK_USED,
                    {
                        "task": req.task.value,
                        "from": backend.name.value,
                        "to": nxt,
                        "reason": fb.reason,
                    },
                )
                continue
            if backend is not self.mock:
                self.health.record_success(backend.name)
                if self.cache is not None and key:
                    self.cache.put(key, backend.name.value, backend.model, req.task.value, output)
            return self._response(req, backend, output, attempts, cached=False, latency=latency)
        raise LLMOutputInvalid("no backend answered")  # pragma: no cover - mock always answers

    def _key(self, backend: Backend, req: LLMRequest, messages: list[dict[str, str]]) -> str:
        if backend is self.mock:
            return ""
        return cache_key(
            backend.name.value, backend.model, req.task.value, messages, req.output_schema
        )

    @staticmethod
    def _response(
        req: LLMRequest,
        backend: Backend,
        output: dict[str, Any],
        attempts: list[LLMBackend],
        *,
        cached: bool,
        latency: int,
    ) -> LLMResponse:
        return LLMResponse(
            task=req.task,
            backend=backend.name,
            model=backend.model,
            output=output,
            cached=cached,
            fallback_used=len(attempts) > 1,
            latency_ms=latency,
            attempts=list(attempts),
        )

    def _refused(self, req: LLMRequest, backend: Backend, exc: RemoteNotAllowed) -> _Fallback:
        self.events(
            EventType.REMOTE_NOT_ALLOWED,
            {
                "task": req.task.value,
                "data_class": req.data_class.name,
                "backend": backend.name.value,
                "reason": exc.message,
            },
        )
        return _Fallback("policy")

    def _try(
        self,
        backend: Backend,
        req: LLMRequest,
        messages: list[dict[str, str]],
        masker: Masker | None,
    ) -> tuple[dict[str, Any], int]:
        send = messages
        active_masker: Masker | None = None
        if backend.remote:
            try:
                policy.assert_remote_allowed(req.task, req.data_class)
                active_masker = masker or self._masker_for(req)
                send = [{**m, "content": active_masker.mask(m["content"])} for m in messages]
                for m in send:
                    assert_no_identifiers(m["content"])
            except RemoteNotAllowed as exc:
                raise self._refused(req, backend, exc) from None
            self._count_remote(req, backend)
        started = time.perf_counter()
        try:
            output = self._call_validated(backend, req, send)
        except RemoteNotAllowed as exc:  # the backend's own, independent check (AGT-01)
            raise self._refused(req, backend, exc) from None
        latency = 0 if backend is self.mock else int((time.perf_counter() - started) * 1000)
        if active_masker is not None:
            output = {
                k: active_masker.unmask(v) if isinstance(v, str) else v for k, v in output.items()
            }
        return output, latency

    def _call_validated(
        self, backend: Backend, req: LLMRequest, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Call once; on invalid output retry once with "Return valid JSON only" (AGT-09)."""
        for attempt in range(2):
            msgs = (
                messages
                if attempt == 0
                else [*messages, {"role": "user", "content": RETRY_INSTRUCTION}]
            )
            try:
                raw = backend.generate(req, msgs)
            except BackendError as exc:
                log.warning(
                    "LLM backend %s failed for %s: %s",
                    backend.name.value,
                    req.task.value,
                    _redact(str(exc)),
                )
                raise _Fallback(exc.reason) from None
            except (TimeoutError, ConnectionError, OSError) as exc:
                log.warning(
                    "LLM backend %s unreachable for %s: %s",
                    backend.name.value,
                    req.task.value,
                    type(exc).__name__,
                )
                raise _Fallback(
                    "timeout" if isinstance(exc, TimeoutError) else "connection"
                ) from None
            try:
                return validate_output(raw, req.output_schema)
            except LLMOutputInvalid:
                log.info(
                    "LLM backend %s gave invalid output (attempt %d)",
                    backend.name.value,
                    attempt + 1,
                )
        raise _Fallback("invalid_json")

    def _masker_for(self, req: LLMRequest) -> Masker:
        names = {
            str(v): kind
            for key, kind in _NAME_KEYS.items()
            if isinstance(v := req.payload.get(key), str) and v
        }
        business = req.payload.get("business_name")
        return Masker(names, business_name=str(business) if business else None)

    def _count_remote(self, req: LLMRequest, backend: Backend) -> None:
        today = get_clock().today()
        self._remote_calls[today] = self._remote_calls.get(today, 0) + 1
        self._remote_event(req, backend, cached=False)

    def _remote_event(self, req: LLMRequest, backend: Backend, *, cached: bool) -> None:
        self.events(
            EventType.LLM_REMOTE_CALL,
            {
                "task": req.task.value,
                "data_class": req.data_class.name,
                "model": backend.model,
                "cached": cached,
            },
        )

    # status --------------------------------------------------------------------------------

    def remote_calls_today(self) -> int:
        return self._remote_calls.get(get_clock().today(), 0)

    def status(self) -> dict[str, Any]:
        """Mode, available backends, models, today's remote calls, per-task routes (what each
        task would try right now, worst-case data class) and the remote budget (UI-17)."""
        available = [n.value for n in self.backends if self.health.available(n)] + [
            LLMBackend.MOCK.value
        ]
        models = {b.name.value: b.model for b in self.backends.values()}
        models[LLMBackend.MOCK.value] = self.mock.model
        routes = {
            t.value: [
                n.value
                for n in routing.resolve(
                    self.mode,
                    t,
                    policy.TASK_POLICY[t].remote_max_class or DataClass.C0_PUBLIC,
                    lambda n: n in self.backends and self.health.available(n),
                    self.overrides,
                )
            ]
            for t in LLMTask
        }
        remote = [b for b in self.backends.values() if b.remote]
        budget = getattr(remote[0], "budget", None) if remote else None
        return {
            "mode": self.mode.upper(),
            "available": available,
            "models": models,
            "remote_calls_today": self.remote_calls_today(),
            "remote_ai": any(LLMBackend.OPENAI.value in r for r in routes.values()),
            "remote_tasks": policy.remote_tasks(),
            "routes": routes,
            "remote_budget": budget.remaining() if budget is not None else None,
        }


class _Fallback(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
