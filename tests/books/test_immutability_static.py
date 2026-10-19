"""T-BKS-13 proves CTL-FR-02, REG-IC-02: no code path updates journal amounts or deletes
journal rows."""

from __future__ import annotations

import re
from pathlib import Path

BOOKS = Path(__file__).resolve().parents[2] / "books"
FORBIDDEN = [
    re.compile(r"\bdelete\s*\(", re.I),
    re.compile(r"DELETE\s+FROM", re.I),
    re.compile(r"UPDATE\s+journal", re.I),
    re.compile(r"update\(\s*Journal", re.I),
    re.compile(r"\.(debit_cents|credit_cents)\s*[+\-*/]?=(?!=)"),
]


def test_no_journal_updates_or_deletes() -> None:
    """T-BKS-13: scan books/*.py for deletes, UPDATE on journal tables, amount writes."""
    problems = []
    for path in sorted(BOOKS.glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("#", 1)[0]
            for pattern in FORBIDDEN:
                if pattern.search(code):
                    problems.append(f"{path.name}:{n}: {line.strip()}")
    assert problems == []


def test_only_status_is_ever_changed() -> None:
    """T-BKS-13: the single journal UPDATE path sets status to REVERSED."""
    text = (BOOKS / "repo.py").read_text(encoding="utf-8")
    assert text.count("s.get(JournalEntry") == 1
    assert "row.status = EntryStatus.REVERSED.value" in text
