"""Local model through Ollama (PRD/09 §2.1). Data never leaves the machine.

``ollama.Client(host).chat(model, messages, format=<JSON schema>, options={temperature 0,
seed 42, num_ctx 2048, num_predict <task allowance>}, keep_alive="10m")``.

Sized for the integration PC (Ryzen 5 3500U, 4 cores, ~4 GB free RAM, 2 GB GPU): the model
stays loaded for 10 minutes so each call does not pay the ~6 s load again, the context is
2,048 tokens and every task has a small output allowance (``MAX_OUTPUT_TOKENS``). The
options never change between calls, because a change makes Ollama reload the model.
Measured with ``phi3:mini``: about 3.5 tokens/s, so a classification takes ~10 s and a
reminder over a minute; the hybrid routes therefore send wording to OpenAI first.
"""

from __future__ import annotations

from typing import Any

from agent.backends import BackendError, max_output_tokens
from contracts.dto_agent import LLMRequest
from contracts.enums import LLMBackend

KEEP_ALIVE = "10m"
NUM_CTX = 2048
SEED = 42


class OllamaBackend:
    name = LLMBackend.OLLAMA
    remote = False

    def __init__(
        self, host: str, model: str, timeout_s: int = 60, client: Any | None = None
    ) -> None:
        self.host = host
        self.model = model
        self.timeout_s = timeout_s
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import ollama

            self._client = ollama.Client(host=self.host, timeout=self.timeout_s)
        return self._client

    def generate(self, req: LLMRequest, messages: list[dict[str, str]]) -> str:
        import httpx

        try:
            import ollama

            response_error: type[Exception] = ollama.ResponseError
        except ImportError:  # pragma: no cover - ollama is in the lock
            response_error = RuntimeError
        try:
            r = self._get_client().chat(
                model=self.model,
                messages=messages,
                format=req.output_schema,
                options={
                    "temperature": 0,
                    "seed": SEED,
                    "num_ctx": NUM_CTX,
                    "num_predict": max_output_tokens(req.task),
                },
                keep_alive=KEEP_ALIVE,
            )
        except (httpx.TimeoutException, TimeoutError) as exc:
            raise BackendError("timeout", type(exc).__name__) from None
        except (httpx.ConnectError, httpx.TransportError, ConnectionError) as exc:
            raise BackendError("connection", type(exc).__name__) from None
        except response_error as exc:
            raise BackendError("unavailable", str(getattr(exc, "error", exc))[:200]) from None
        message = r["message"] if isinstance(r, dict) else r.message
        content = message["content"] if isinstance(message, dict) else message.content
        return str(content or "")
