"""Categorisation pipeline (PRD/05 §3, BKS-05..BKS-07, CTL-FR-06).

Order: (1) first matching rule; (2) if none, the injected ``Classifier`` with the
candidate list (every income/expense account code + PERSONAL); (3) a label outside the
candidates becomes 6900 with confidence 0 (FALLBACK); (4) confidence below the threshold
or a PERSONAL label goes to the owner's confirm queue.
"""

from __future__ import annotations

from decimal import Decimal

from books.rules import FALLBACK_LABEL, Rule, match_rule
from contracts.dto_books import CategorisationResult
from contracts.enums import AccountType, CategorisationMethod, LLMTask, SourceKind
from contracts.registry import CHART_OF_ACCOUNTS, PERSONAL_LABEL
from contracts.services import Classifier
from contracts.untrusted import Untrusted

CANDIDATES: list[str] = [
    a.code for a in CHART_OF_ACCOUNTS if a.type in (AccountType.INCOME, AccountType.EXPENSE)
] + [PERSONAL_LABEL]


def categorise_text(
    raw_id: str,
    description: str,
    rules: list[Rule],
    classifier: Classifier,
    threshold: Decimal,
) -> CategorisationResult:
    rule = match_rule(rules, description)
    if rule is not None:
        label, conf = rule.label, rule.confidence
        method, rule_id = CategorisationMethod.RULE, rule.id
    else:
        res = classifier.classify(
            Untrusted(description, SourceKind.BANK, raw_id), CANDIDATES, LLMTask.CATEGORISE_TXN
        )
        label, conf, method, rule_id = res.label, res.confidence, res.method, None
        if label not in CANDIDATES:  # BKS-06
            label, conf, method = FALLBACK_LABEL, Decimal(0), CategorisationMethod.FALLBACK
    needs = conf < threshold or label == PERSONAL_LABEL
    return CategorisationResult(
        raw_txn_id=raw_id,
        label=label,
        confidence=conf,
        method=method,
        needs_confirmation=needs,
        rule_id=rule_id,
    )
