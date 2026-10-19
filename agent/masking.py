"""Masking before remote calls (PRD/09 §4, CTL-AI-03, REG-DP-01, REG-DP-02).

``Masker`` swaps known counterparty names for stable placeholders and strips identifiers;
``assert_no_identifiers`` refuses a payload that still looks like an account, email or phone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from contracts.enums import CounterpartyKind
from contracts.errors import RemoteNotAllowed

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# International (+999 ...) or bracketed-area numbers; plain long digit runs are {NUM}.
PHONE_RE = re.compile(r"(?:\+\d{1,3}|\(\d{2,4}\))[\d\s\-()]{6,}\d")
LONG_DIGITS_RE = re.compile(r"\d{6,}")
ACCOUNT_RES = (
    re.compile(r"\b\d{8,34}\b"),
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", re.IGNORECASE),
    re.compile(r"\bSBX-[A-Z]{2}-\d{6,}\b", re.IGNORECASE),
)

_PREFIX = {
    CounterpartyKind.CUSTOMER: "CUSTOMER",
    CounterpartyKind.SUPPLIER: "SUPPLIER",
    CounterpartyKind.OTHER: "PARTY",
}
BUSINESS_PLACEHOLDER = "{BUSINESS}"


class Masker:
    """Replaces names (case-insensitive, longest first) with ``{CUSTOMER_n}`` / ``{SUPPLIER_n}``
    (numbered by sorted name per kind) and the business name with ``{BUSINESS}``; removes digit
    runs of 6+ (``{NUM}``), emails (``{EMAIL}``) and phones (``{PHONE}``).

    ``unmask`` restores the registered spelling of each name; removed identifiers stay removed.
    """

    def __init__(
        self, known_names: Mapping[str, CounterpartyKind], business_name: str | None = None
    ) -> None:
        self._to_placeholder: dict[str, str] = {}
        self._to_name: dict[str, str] = {}
        for kind, prefix in _PREFIX.items():
            names = sorted({n for n, k in known_names.items() if k == kind and n.strip()})
            for i, name in enumerate(names, start=1):
                self._register(name, f"{{{prefix}_{i}}}")
        if business_name and business_name.strip():
            self._register(business_name, BUSINESS_PLACEHOLDER)
        ordered = sorted(self._to_placeholder, key=lambda n: (-len(n), n))
        self._names_re = (
            re.compile("|".join(rf"(?<!\w){re.escape(n)}(?!\w)" for n in ordered), re.IGNORECASE)
            if ordered
            else None
        )
        self._placeholder_re = re.compile(
            "|".join(re.escape(p) for p in sorted(self._to_name, key=len, reverse=True))
            or r"(?!x)x"
        )

    def _register(self, name: str, placeholder: str) -> None:
        key = name.casefold()
        if key in self._to_placeholder:
            return
        self._to_placeholder[key] = placeholder
        self._to_name[placeholder] = name

    def placeholder_for(self, name: str) -> str:
        return self._to_placeholder[name.casefold()]

    def mask(self, text: str) -> str:
        out = str(text)
        if self._names_re is not None:
            out = self._names_re.sub(lambda m: self._to_placeholder[m.group(0).casefold()], out)
        out = EMAIL_RE.sub("{EMAIL}", out)
        out = PHONE_RE.sub("{PHONE}", out)
        return LONG_DIGITS_RE.sub("{NUM}", out)

    def unmask(self, text: str) -> str:
        return self._placeholder_re.sub(lambda m: self._to_name[m.group(0)], str(text))


def assert_no_identifiers(text: str) -> None:
    """Refuse (``RemoteNotAllowed``) if masked text still holds an account, email or phone
    pattern (AGT-05)."""
    patterns = (*ACCOUNT_RES, EMAIL_RE, PHONE_RE)
    for pattern in patterns:
        if pattern.search(text):
            raise RemoteNotAllowed(
                "payload still contains an identifier after masking",
                details={"pattern": pattern.pattern[:40]},
            )
