"""Remote model through the OpenAI API (PRD/09 §2.1, OPTIONAL module; CTL-AI-02, CTL-AI-07).

Only for tasks and data classes the policy allows (§3). Independently of route resolution,
``generate`` re-checks the policy **before any network call** and raises ``RemoteNotAllowed``
(AGT-01). The ``Budget`` reserves the call's worst-case cost before calling and books the
real token usage after (AGT-13), so prepaid credit cannot be overspent.

Request: chat completions with structured outputs (``response_format`` JSON schema,
``strict: true``), ``max_completion_tokens`` from the task allowance, ``temperature=0`` and
low reasoning effort when the model accepts them: a parameter the API reports as unsupported
is dropped and the call retried once per parameter. The API key comes from settings
(``OPENAI_API_KEY``) and is never logged, cached or put in an exception.
"""

from __future__ import annotations

import logging
from typing import Any

from agent import policy
from agent.backends import BackendError, max_output_tokens, strict_schema
from agent.budget import Budget, BudgetExhausted, estimate_tokens
from contracts.dto_agent import LLMRequest
from contracts.enums import LLMBackend

log = logging.getLogger(__name__)

# Optional request parameters, dropped one by one if the model rejects them.
OPTIONAL_PARAMS: dict[str, Any] = {"temperature": 0, "reasoning_effort": "low"}


class OpenAIBackend:
    name = LLMBackend.OPENAI
    remote = True

    def __init__(
        self,
        api_key: str,
        model: str,
        budget: Budget,
        timeout_s: int = 30,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self.budget = budget
        self.timeout_s = timeout_s
        self._client = client
        self._dropped: set[str] = set()

    def __repr__(self) -> str:  # never show the key
        return f"OpenAIBackend(model={self.model!r})"

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key, timeout=self.timeout_s, max_retries=1)
        return self._client

    def _kwargs(self, req: LLMRequest, messages: list[dict[str, str]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_output_tokens(req.task),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": req.task.value.lower(),
                    "schema": strict_schema(req.output_schema),
                    "strict": True,
                },
            },
        }
        kwargs.update({k: v for k, v in OPTIONAL_PARAMS.items() if k not in self._dropped})
        return kwargs

    def generate(self, req: LLMRequest, messages: list[dict[str, str]]) -> str:
        policy.assert_remote_allowed(req.task, req.data_class)  # before any network call
        try:
            self.budget.reserve(estimate_tokens(messages), max_output_tokens(req.task))
        except BudgetExhausted as exc:
            raise BackendError("budget", exc.reason) from None
        import openai

        for _ in range(len(OPTIONAL_PARAMS) + 1):
            try:
                resp = self._get_client().chat.completions.create(**self._kwargs(req, messages))
                break
            except openai.BadRequestError as exc:
                text = str(getattr(exc, "message", "")) or str(exc)
                bad = next((p for p in OPTIONAL_PARAMS if p in text), None)
                if bad is None or bad in self._dropped:
                    raise BackendError("refused", type(exc).__name__) from None
                log.info("OpenAI model %s does not accept %s; retrying without", self.model, bad)
                self._dropped.add(bad)
            except openai.APITimeoutError:
                raise BackendError("timeout") from None
            except openai.APIConnectionError:
                raise BackendError("connection") from None
            except openai.RateLimitError as exc:
                code = getattr(exc, "code", None) or ""
                reason = "budget" if "quota" in str(code) else "unavailable"
                raise BackendError(reason, type(exc).__name__) from None
            except openai.APIError as exc:
                raise BackendError("unavailable", type(exc).__name__) from None
        else:  # pragma: no cover - every optional parameter dropped and still refused
            raise BackendError("refused")
        usage = getattr(resp, "usage", None)
        self.budget.record(
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise BackendError("refused", "model refused")
        return str(choice.message.content or "")
