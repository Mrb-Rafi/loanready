"""Post-check of any message wording against the facts (PRD/08 §3 step 2, CTL-AI-01,
REG-COM-01). The LLM may only word text; every number, date and reference is checked
here in code. A failure means the template text is used instead.
"""

from __future__ import annotations

import re

from contracts.dto_outputs import DelayFacts, ReminderFacts

MAX_LENGTH = 1200
_AMOUNT_RES = (
    re.compile(r"[$€£¥]\s?\d[\d,]*(?:\.\d{2})?"),
    re.compile(r"\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP|CNY)"),
)
_URL = re.compile(r"https?://|www\.", re.I)
_ACCOUNT = re.compile(r"\b\d{8,34}\b|\bSBX-[A-Z]{2}-\d{4,}\b|\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
THREAT_WORDS = ("court", "lawsuit", "police", "legal action", "blacklist")


def _common(text: str, amount_str: str) -> list[str]:
    problems = []
    for pattern in _AMOUNT_RES:
        for m in pattern.finditer(text):
            if m.group(0).replace(" ", "") != amount_str:
                problems.append(f"unexpected amount {m.group(0)!r}")
    if _URL.search(text):
        problems.append("contains a URL")
    if _ACCOUNT.search(text):
        problems.append("contains a bank account pattern")
    if _PHONE.search(text):
        problems.append("contains a phone number")
    if len(text) > MAX_LENGTH:
        problems.append(f"longer than {MAX_LENGTH} characters")
    lower = text.lower()
    for word in THREAT_WORDS:
        if word in lower:
            problems.append(f"threat or legal language: {word!r}")
    return problems


def _require(text: str, values: dict[str, str]) -> list[str]:
    return [f"missing {k} {v!r}" for k, v in values.items() if v not in text]


def check_reminder(subject: str, body: str, facts: ReminderFacts) -> list[str]:
    """[] when the wording passes; else the list of problems."""
    text = f"{subject}\n{body}"
    return _require(
        text,
        {
            "invoice number": facts.invoice_number,
            "amount": facts.amount_str,
            "due date": facts.due_date_str,
        },
    ) + _common(text, facts.amount_str)


def check_delay(subject: str, body: str, facts: DelayFacts) -> list[str]:
    text = f"{subject}\n{body}"
    return _require(
        text,
        {
            "bill number": facts.bill_number,
            "amount": facts.amount_str,
            "due date": facts.due_date_str,
            "proposed date": facts.proposed_date_str,
        },
    ) + _common(text, facts.amount_str)
