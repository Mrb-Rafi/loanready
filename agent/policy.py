"""Data-class policy for model calls (PRD/09 §3, CTL-AI-02, REG-DP-01..04, REG-AI-03 LLM02).

Classes: C0 public, C1 aggregate, C2 counterparty names (remote only masked), C3 bank
identifiers and C4 documents (never remote). Checks here are plain code; the model never
decides what may leave the machine.
"""

from __future__ import annotations

from dataclasses import dataclass

from contracts.enums import DataClass, LLMBackend, LLMTask
from contracts.errors import InvalidParams, RemoteNotAllowed


@dataclass(frozen=True)
class TaskPolicy:
    max_class: DataClass  # highest class a request for this task may carry at all
    local_allowed: bool
    remote_max_class: DataClass | None  # None = never remote
    note: str


TASK_POLICY: dict[LLMTask, TaskPolicy] = {
    LLMTask.CATEGORISE_TXN: TaskPolicy(
        DataClass.C4_DOCUMENT,
        True,
        DataClass.C2_COUNTERPARTY,
        "merchant name only, digits masked; full memo never remote",
    ),
    LLMTask.PERSONAL_VS_BUSINESS: TaskPolicy(
        DataClass.C4_DOCUMENT,
        True,
        DataClass.C2_COUNTERPARTY,
        "merchant name only, digits masked; below threshold the owner decides",
    ),
    LLMTask.DRAFT_REMINDER: TaskPolicy(
        DataClass.C2_COUNTERPARTY,
        True,
        DataClass.C2_COUNTERPARTY,
        "names masked; post-check in WP6",
    ),
    LLMTask.DRAFT_DELAY_REQUEST: TaskPolicy(
        DataClass.C2_COUNTERPARTY,
        True,
        DataClass.C2_COUNTERPARTY,
        "names masked; post-check in WP6",
    ),
    LLMTask.EXPLAIN_FORECAST: TaskPolicy(
        DataClass.C1_AGGREGATE, True, DataClass.C1_AGGREGATE, "numbers from code only"
    ),
    LLMTask.EXPLAIN_FINANCING: TaskPolicy(
        DataClass.C1_AGGREGATE, True, DataClass.C1_AGGREGATE, "numbers from code only"
    ),
    LLMTask.TIDY_RECEIPT_TEXT: TaskPolicy(DataClass.C4_DOCUMENT, True, None, "local only"),
    LLMTask.INTENT_PICK: TaskPolicy(
        DataClass.C2_COUNTERPARTY, True, None, "user's own message treated as C2; local only"
    ),
}

REMOTE_BACKENDS: frozenset[LLMBackend] = frozenset({LLMBackend.OPENAI})


def is_remote(backend: LLMBackend) -> bool:
    return backend in REMOTE_BACKENDS


def check_request(task: LLMTask, data_class: DataClass) -> None:
    """Refuse a request whose data class is above the task's maximum (AGT-01)."""
    pol = TASK_POLICY[task]
    if data_class > pol.max_class:
        raise InvalidParams(
            f"data class {data_class.name} exceeds {pol.max_class.name} for task {task.value}",
            details={"task": task.value, "data_class": data_class.name},
        )


def remote_allowed(task: LLMTask, data_class: DataClass) -> bool:
    """True only if the task may go remote and the class is within its remote limit.
    C3 and C4 are never remote, whatever the table says."""
    pol = TASK_POLICY[task]
    if pol.remote_max_class is None or data_class >= DataClass.C3_BANK_ID:
        return False
    return data_class <= pol.remote_max_class


def assert_remote_allowed(task: LLMTask, data_class: DataClass) -> None:
    """Second, independent guard run right before any remote backend call (AGT-01)."""
    if not remote_allowed(task, data_class):
        raise RemoteNotAllowed(
            f"task {task.value} with {data_class.name} may not leave the machine",
            details={"task": task.value, "data_class": data_class.name},
        )


def remote_tasks() -> list[str]:
    """Tasks that may use a remote model at all (shown in the UI next to "Remote AI: on")."""
    return [t.value for t, p in TASK_POLICY.items() if p.remote_max_class is not None]
