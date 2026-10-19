"""Rule-first categorisation (PRD/05 §3): ``data/rules.json`` and the rules-only
``RuleClassifier`` used when the agent (WP7) is absent.

Rules are regexes matched, in list order, against ``normalize_for_scan(description)``
uppercased. The first match wins (BKS-05).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from contracts.dto_books import CategorisationResult
from contracts.enums import CategorisationMethod, LLMTask
from contracts.untrusted import Untrusted, normalize_for_scan
from infra.paths import DATA_DIR

RULES_FILE = DATA_DIR / "rules.json"
FALLBACK_LABEL = "6900"


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    label: str
    confidence: Decimal


def load_rules(path: Path = RULES_FILE) -> list[Rule]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Rule(r["id"], re.compile(r["pattern"]), r["label"], Decimal(r["confidence"]))
        for r in data["rules"]
    ]


def normalised(text: str) -> str:
    """The text rules are matched against: NFKC, casefold, homoglyphs, uppercased."""
    return normalize_for_scan(text).upper()


def match_rule(rules: list[Rule], description: str) -> Rule | None:
    text = normalised(description)
    return next((r for r in rules if r.pattern.search(text)), None)


# Extra keywords for the rules-only classifier (BKS-07); confidence is always 0.60 so the
# owner confirms every guess.
_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("SUPPLY", "5000"),
    ("DELIVERY", "5000"),
    ("BAKERY", "5000"),
    ("ADVERT", "6300"),
    ("MARKETING", "6300"),
    ("INTEREST", "7000"),
    ("INSURANCE", "6900"),
    ("REPAIR", "6900"),
)
RULE_CLASSIFIER_CONFIDENCE = Decimal("0.60")


class RuleClassifier:
    """Rules-only ``Classifier`` (no LLM). Picks a keyword label if it is a candidate, else
    6900, always with confidence 0.60 (below the 0.80 threshold, so it is confirmed)."""

    def classify(
        self, text: Untrusted, candidates: list[str], task: LLMTask
    ) -> CategorisationResult:
        upper = normalised(text)
        label = next(
            (code for word, code in _KEYWORDS if word in upper and code in candidates),
            FALLBACK_LABEL,
        )
        return CategorisationResult(
            raw_txn_id=text.source_ref,
            label=label,
            confidence=RULE_CLASSIFIER_CONFIDENCE,
            method=CategorisationMethod.FALLBACK,
            needs_confirmation=True,
            rule_id=None,
        )
