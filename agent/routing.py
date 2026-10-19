"""Per-task backend routes (PRD/09 §2.2, AGT-19).

``resolve(mode, task, data_class, available)`` starts from the mode's list (for ``hybrid``,
``DEFAULT_ROUTES[task]``, optionally overridden per task by the JSON file named in
``LLM_ROUTES_FILE``), removes every remote backend the data-class policy forbids for this
task and class (so a misconfigured route can never send C3/C4 data out), removes
unavailable backends and ends with ``MOCK``. The result is never empty.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path

from agent import policy
from contracts.enums import DataClass, LLMBackend, LLMTask

LOCAL, REMOTE_AI = LLMBackend.OLLAMA, LLMBackend.OPENAI
REMOTE = frozenset({LLMBackend.OPENAI})

DEFAULT_ROUTES: dict[LLMTask, tuple[LLMBackend, ...]] = {
    LLMTask.DRAFT_REMINDER: (REMOTE_AI, LOCAL),
    LLMTask.DRAFT_DELAY_REQUEST: (REMOTE_AI, LOCAL),
    LLMTask.EXPLAIN_FORECAST: (REMOTE_AI, LOCAL),
    LLMTask.EXPLAIN_FINANCING: (REMOTE_AI, LOCAL),
    LLMTask.CATEGORISE_TXN: (LOCAL, REMOTE_AI),
    LLMTask.PERSONAL_VS_BUSINESS: (LOCAL, REMOTE_AI),
    LLMTask.TIDY_RECEIPT_TEXT: (LOCAL,),
    LLMTask.INTENT_PICK: (LOCAL,),
}

MODES = ("mock", "ollama", "openai", "hybrid")


def load_overrides(path: str | Path | None) -> dict[LLMTask, tuple[LLMBackend, ...]]:
    """``{"DRAFT_REMINDER": ["OLLAMA", "OPENAI"], ...}``; unknown task or backend names raise
    ``ValueError`` (validated against the same names as DEFAULT_ROUTES)."""
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("LLM_ROUTES_FILE must hold a JSON object of task -> [backends]")
    out: dict[LLMTask, tuple[LLMBackend, ...]] = {}
    for task, names in data.items():
        if not isinstance(names, list):
            raise ValueError(f"route for {task} must be a list")
        out[LLMTask(task)] = tuple(LLMBackend(str(n).upper()) for n in names)
    return out


def mode_route(
    mode: str,
    task: LLMTask,
    overrides: Mapping[LLMTask, tuple[LLMBackend, ...]] | None = None,
) -> tuple[LLMBackend, ...]:
    if mode == "mock":
        return ()
    if mode == "ollama":
        return (LOCAL,)
    if mode == "openai":
        return (REMOTE_AI,)
    if mode == "hybrid":
        return tuple((overrides or {}).get(task, DEFAULT_ROUTES[task]))
    raise ValueError(f"unknown LLM_BACKEND {mode!r}")


def resolve(
    mode: str,
    task: LLMTask,
    data_class: DataClass,
    available: Callable[[LLMBackend], bool],
    overrides: Mapping[LLMTask, tuple[LLMBackend, ...]] | None = None,
) -> list[LLMBackend]:
    chain: list[LLMBackend] = []
    for name in mode_route(mode, task, overrides):
        if name == LLMBackend.MOCK or name in chain:
            continue
        if name in REMOTE and not policy.remote_allowed(task, data_class):
            continue
        if not available(name):
            continue
        chain.append(name)
    chain.append(LLMBackend.MOCK)
    return chain
