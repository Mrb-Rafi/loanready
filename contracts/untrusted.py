"""Untrusted text (CON-05).

Text from receipts, invoices, bills, emails, bank memos and POS notes is never
instruction-bearing. Scanning here is **visibility only**: the defence is structural (the
gate checks origin and provenance; entities come from stored records).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import Field, GetCoreSchemaHandler
from pydantic_core import core_schema

from contracts.enums import SourceKind

UntrustedText = Annotated[str, Field(json_schema_extra={"untrusted": True})]

UNTRUSTED_FIELDS = frozenset(
    {"description", "memo", "note", "ocr_text", "claimed_bank_account_ref", "body_in", "vendor_raw"}
)

EXCERPT_MAX = 80


class Untrusted(str):
    """Text from receipts, invoices, emails, bank memos, POS notes, supplier-supplied fields.
    Never instruction-bearing. Only agent.prompts.data_block() may put it into a prompt."""

    source: SourceKind
    source_ref: str

    def __new__(cls, value: str, source: SourceKind, source_ref: str) -> Untrusted:
        obj = super().__new__(cls, value)
        obj.source = SourceKind(source)
        obj.source_ref = str(source_ref)
        return obj

    def __reduce__(self) -> tuple[Any, ...]:
        return (Untrusted, (str(self), self.source, self.source_ref))

    def __repr__(self) -> str:
        return f"Untrusted({str(self)[:40]!r}, source={self.source.value}, ref={self.source_ref!r})"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source: Any, _handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Inside pydantic models an Untrusted round-trips as {"value","source","source_ref"}."""

        def from_dict(data: dict[str, Any]) -> Untrusted:
            return Untrusted(data["value"], SourceKind(data["source"]), data["source_ref"])

        dict_schema = core_schema.chain_schema(
            [
                core_schema.typed_dict_schema(
                    {
                        "value": core_schema.typed_dict_field(core_schema.str_schema()),
                        "source": core_schema.typed_dict_field(core_schema.str_schema()),
                        "source_ref": core_schema.typed_dict_field(core_schema.str_schema()),
                    }
                ),
                core_schema.no_info_plain_validator_function(from_dict),
            ]
        )
        return core_schema.union_schema(
            [core_schema.is_instance_schema(cls), dict_schema],
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda u: {"value": str(u), "source": u.source.value, "source_ref": u.source_ref}
            ),
        )


def as_untrusted(value: str, source: SourceKind, source_ref: str) -> Untrusted:
    """Wrap a plain ``UntrustedText`` DTO field at the point of use."""
    if isinstance(value, Untrusted):
        return value
    return Untrusted(value, source, source_ref)


@dataclass(frozen=True)
class ScanFlag:
    pattern_id: str
    excerpt: str  # at most 80 chars around the match
    source: SourceKind
    source_ref: str


_ZERO_WIDTH = dict.fromkeys([*range(0x200B, 0x2010), 0x2060, 0xFEFF])
# Common Cyrillic/Greek look-alikes of Latin letters (after casefold).
_HOMOGLYPHS = str.maketrans(
    {
        "а": "a",
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "і": "i",
        "ј": "j",
        "ѕ": "s",
        "у": "y",
        "ԁ": "d",
        "һ": "h",
        "ο": "o",
        "α": "a",
        "ε": "e",
        "ι": "i",
        "ν": "v",
        "τ": "t",
    }
)
_WS = re.compile(r"\s+")


def normalize_for_scan(text: str) -> str:
    """NFKC, strip zero-width chars, casefold, map homoglyphs, collapse whitespace."""
    out = unicodedata.normalize("NFKC", str(text))
    out = out.translate(_ZERO_WIDTH)
    out = out.casefold().translate(_HOMOGLYPHS)
    return _WS.sub(" ", out).strip()


_F = "(?:all|any|the|previous|prior|above|your|these|those|my|earlier)"
INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "IGNORE_PREVIOUS",
        re.compile(rf"\bignore\W+(?:{_F}\W+){{0,3}}(?:instructions|rules|directions)\b"),
    ),
    (
        "DISREGARD",
        re.compile(rf"\bdisregard\W+(?:{_F}\W+){{0,3}}(?:instructions|rules|messages?|above)\b"),
    ),
    (
        "NEW_INSTRUCTIONS",
        re.compile(r"\b(?:new|updated|follow(?:ing)?\W+(?:these|the|my))\W+instructions\b"),
    ),
    ("SYSTEM_PROMPT", re.compile(r"\bsystem\W*prompt\b|\bdeveloper\W+message\b")),
    ("ROLE_SWITCH", re.compile(r"\byou are now\b|\bact as\b|\bpretend to be\b")),
    (
        "TRANSFER_FUNDS",
        re.compile(
            r"\b(?:transfer|send|wire|move|pay)\W+(?:(?:all|the)\W+){0,2}(?:funds|money|balance)\b"
        ),
    ),
    (
        "PAY_TO_ACCOUNT",
        re.compile(r"\bpay(?:ment)?\W+(?:to|into)\W+(?:the\W+)?(?:new\W+)?(?:account|acct|iban)\b"),
    ),
    ("CHANGE_BANK", re.compile(r"\b(?:new|updated|changed)\W+(?:bank\W+)?(?:account|details)\b")),
    (
        "ACCOUNT_NUMBER",
        re.compile(r"\b\d{8,34}\b|\b[a-z]{2}\d{2}[a-z0-9]{10,30}\b|\bsbx-[a-z]{2}-\d{6,}\b"),
    ),
    ("APPROVE_ALL", re.compile(r"\bapprove\W+(?:all|every|this)\b|\bauto-?\W?approve")),
    (
        "DISABLE_SAFETY",
        re.compile(
            r"\b(?:disable|turn off|bypass)\W+(?:the\W+)?(?:gate|safety|limits?|kill\W?switch)\b"
        ),
    ),
    (
        "RAISE_LIMIT",
        re.compile(r"\b(?:raise|increase|remove)\W+(?:the\W+)?(?:\w+\W+)?(?:limit|cap)s?\b"),
    ),
    ("TOOL_CALL_JSON", re.compile(r'\{\s*"(?:action|tool|function)"\s*:')),
    ("URGENT_PRESSURE", re.compile(r"(?:urgent|immediately|within 1 hour).{0,40}(?:pay|transfer)")),
    (
        "IGNORE_PREVIOUS_ZH",
        re.compile(r"忽略.{0,6}(?:之前|以上|先前|所有).{0,6}(?:指令|指示|规则)"),
    ),
    ("TRANSFER_ZH", re.compile(r"(?:转账|汇款|转到|转入|付款).{0,12}(?:账户|账号)")),
]


def _excerpt(text: str, start: int, end: int) -> str:
    pad = max(0, (EXCERPT_MAX - (end - start)) // 2)
    lo = max(0, start - pad)
    return text[lo : lo + EXCERPT_MAX]


def scan_untrusted(text: Untrusted) -> list[ScanFlag]:
    """One flag per matching pattern id (first match), on the normalised text."""
    if not isinstance(text, Untrusted):
        raise TypeError("scan_untrusted expects an Untrusted value")
    norm = normalize_for_scan(text)
    flags: list[ScanFlag] = []
    for pattern_id, pattern in INJECTION_PATTERNS:
        match = pattern.search(norm)
        if match:
            flags.append(
                ScanFlag(
                    pattern_id=pattern_id,
                    excerpt=_excerpt(norm, match.start(), match.end()),
                    source=text.source,
                    source_ref=text.source_ref,
                )
            )
    return flags
