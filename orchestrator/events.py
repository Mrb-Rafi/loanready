"""EventSink (PRD/11 §1): forwards agent, books and orchestrator events to the audit log.

The agent receives the sink as ``events: Callable[[EventType, dict], None]`` (PRD/09 §2).
Both the agent (AGT-11) and the orchestrator's sync path may report the same flagged text,
so ``UNTRUSTED_INSTRUCTION_IGNORED`` and ``DUPLICATE_FLAGGED`` are logged once per source
reference for the life of the sink.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from contracts.actions import AGENT, SYSTEM, Principal
from contracts.enums import EventType
from contracts.services import AuditLogService
from contracts.untrusted import ScanFlag

EVENT_CONTROLS: dict[EventType, tuple[str, ...]] = {
    EventType.UNTRUSTED_INSTRUCTION_IGNORED: ("CTL-AI-04",),
    EventType.LLM_FALLBACK_USED: ("CTL-AI-05",),
    EventType.LLM_REMOTE_CALL: ("CTL-AI-02", "CTL-AI-07"),
    EventType.REMOTE_NOT_ALLOWED: ("CTL-AI-02",),
    EventType.KILL_SWITCH_TOGGLED: ("CTL-AC-07",),
    EventType.SETTINGS_CHANGED: ("CTL-CHG-01",),
    EventType.ITEM_CONFIRMED: ("CTL-FR-06",),
    EventType.DUPLICATE_FLAGGED: ("CTL-FR-05",),
    EventType.PERIOD_CLOSED: ("CTL-FR-03",),
    EventType.SEED_LOADED: ("CTL-LOG-01",),
    EventType.CHAIN_VERIFIED: ("CTL-LOG-02",),
    EventType.EXPORT_CREATED: ("CTL-LOG-03",),
}
_ONCE_PER_REF = (EventType.UNTRUSTED_INSTRUCTION_IGNORED, EventType.DUPLICATE_FLAGGED)


class EventSink:
    """Callable ``sink(event, details)``; the default principal is the agent."""

    def __init__(self, audit: AuditLogService, principal: Principal = AGENT):
        self.audit = audit
        self.principal = principal
        self._seen: set[tuple[str, str, str]] = set()
        # events already in the log count as seen, so a restarted app does not re-log them
        for row in audit.rows():
            if row.action_type in {e.value for e in _ONCE_PER_REF}:
                self._seen.add(
                    (
                        row.action_type,
                        str(row.params_masked.get("source", "")),
                        str(row.params_masked.get("source_ref", "")),
                    )
                )

    def __call__(self, event: EventType, details: dict[str, Any]) -> None:
        self.log(event, details)

    def log(
        self,
        event: EventType,
        details: dict[str, Any],
        principal: Principal | None = None,
        control_ids: Sequence[str] | None = None,
    ) -> int | None:
        """Append one EVENT row; returns its seq, or None when it was a repeat."""
        event = EventType(event)
        if event in _ONCE_PER_REF:
            key = (
                event.value,
                str(details.get("source", "")),
                str(details.get("source_ref", "")),
            )
            if key in self._seen:
                return None
            self._seen.add(key)
        ctl = list(control_ids) if control_ids is not None else list(EVENT_CONTROLS.get(event, ()))
        return self.audit.log_event(event, details, principal or self.principal, ctl)

    def flags(self, flags: Sequence[ScanFlag], source: str, source_ref: str) -> int:
        """Log ``UNTRUSTED_INSTRUCTION_IGNORED`` for one flagged text; returns 1 if it was
        flagged. ``agent.scan`` may already have logged the same event (then this is a repeat)."""
        if not flags:
            return 0
        self.log(
            EventType.UNTRUSTED_INSTRUCTION_IGNORED,
            {
                "source": source,
                "source_ref": source_ref,
                "pattern_ids": sorted({f.pattern_id for f in flags}),
            },
            SYSTEM,
        )
        return 1
