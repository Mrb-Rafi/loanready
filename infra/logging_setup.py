"""Logging with a redaction filter (CTL-SEC-01, ARC-25).

Operational logs never contain tokens, API keys, bearer headers or full account numbers.
"""

from __future__ import annotations

import logging
import re

from pydantic import SecretStr

_PATTERNS = [
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[redacted-key]"),
    (re.compile(r"\bsbx-(?:read|write|admin)-[A-Za-z0-9_-]+"), "[redacted-token]"),
    (re.compile(r"\bSBX-[A-Z]{2}-\d{4}(\d{4})\b"), r"****\1"),
    (re.compile(r"\b\d{6,30}(\d{4})\b"), r"****\1"),
]


def _secret_values() -> list[str]:
    """Current secret setting values (any SecretStr field), longest first."""
    try:
        from infra.settings import get_settings

        s = get_settings()
    except Exception:
        return []
    values = []
    for name in type(s).model_fields:
        v = getattr(s, name)
        if isinstance(v, SecretStr) and len(v.get_secret_value()) >= 6:
            values.append(v.get_secret_value())
    return sorted(values, key=len, reverse=True)


def redact(text: str) -> str:
    for secret in _secret_values():
        text = text.replace(secret, "[redacted]")
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        record.msg = redact(message)
        record.args = None
        return True


def configure_logging(level: int | str = logging.INFO) -> None:
    """Root logging with the redaction filter on every handler."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)
    for handler in root.handlers:
        if not any(isinstance(f, RedactionFilter) for f in handler.filters):
            handler.addFilter(RedactionFilter())
