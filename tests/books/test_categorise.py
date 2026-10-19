"""T-BKS-04 proves BKS-05, BKS-06, BKS-07, CTL-FR-06: rule order, classifier fallback,
labels outside the candidates, confidence threshold."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from books.categorise import CANDIDATES, categorise_text
from books.rules import RuleClassifier, load_rules
from contracts.dto_books import CategorisationResult
from contracts.enums import CategorisationMethod, ConfirmKind, LLMTask, RawStatus
from contracts.sandbox_api import BankTxnOut
from contracts.untrusted import Untrusted

RULES = load_rules()
T = Decimal("0.80")


class FixedClassifier:
    def __init__(self, label: str, conf: str):
        self.label, self.conf = label, Decimal(conf)
        self.calls: list[tuple[str, list[str], LLMTask]] = []

    def classify(
        self, text: Untrusted, candidates: list[str], task: LLMTask
    ) -> CategorisationResult:
        self.calls.append((str(text), candidates, task))
        return CategorisationResult(
            raw_txn_id=text.source_ref,
            label=self.label,
            confidence=self.conf,
            method=CategorisationMethod.LLM,
            needs_confirmation=False,
            rule_id=None,
        )


def test_first_matching_rule_wins() -> None:
    """T-BKS-04: rules in list order on the normalised, uppercased text."""
    res = categorise_text("r", "rent - maple properties", RULES, RuleClassifier(), T)
    assert (res.label, res.rule_id, res.method) == ("6000", "R-RENT", CategorisationMethod.RULE)
    assert not res.needs_confirmation
    assert categorise_text("r", "GRAIN DEPOT WHOLESALE", RULES, RuleClassifier(), T).label == "5000"
    assert categorise_text("r", "NETLINK INTERNET", RULES, RuleClassifier(), T).label == "6200"
    equip = categorise_text(
        "r", "RIVERTON RESTAURANT SUPPLY - DECK OVEN", RULES, RuleClassifier(), T
    )
    assert equip.label == "1500" and not equip.needs_confirmation


def test_classifier_used_only_without_rule() -> None:
    """T-BKS-04: the classifier gets every income/expense code + PERSONAL."""
    clf = FixedClassifier("6300", "0.95")
    res = categorise_text("r", "UNKNOWN MERCHANT 42", RULES, clf, T)
    assert res.label == "6300" and res.method == CategorisationMethod.LLM
    assert not res.needs_confirmation
    assert clf.calls[0][1] == CANDIDATES and clf.calls[0][2] == LLMTask.CATEGORISE_TXN
    assert "PERSONAL" in CANDIDATES and "1000" not in CANDIDATES
    categorise_text("r", "PAYROLL", RULES, clf, T)
    assert len(clf.calls) == 1


def test_label_outside_candidates_is_fallback() -> None:
    """T-BKS-04 / BKS-06: a label outside the list -> 6900, confidence 0, confirm."""
    res = categorise_text("r", "UNKNOWN", RULES, FixedClassifier("1000", "0.99"), T)
    assert (res.label, res.confidence, res.method) == (
        "6900",
        Decimal(0),
        CategorisationMethod.FALLBACK,
    )
    assert res.needs_confirmation


def test_threshold() -> None:
    """T-BKS-04: below 0.80 -> confirm; at 0.80 -> no confirm."""
    assert categorise_text("r", "X", RULES, FixedClassifier("6900", "0.79"), T).needs_confirmation
    assert not categorise_text(
        "r", "X", RULES, FixedClassifier("6900", "0.80"), T
    ).needs_confirmation


def test_rule_classifier_always_asks() -> None:
    """T-BKS-04 / BKS-07: the rules-only classifier answers with confidence 0.60."""
    res = RuleClassifier().classify(
        Untrusted("CORNER HARDWARE REPAIR", "BANK", "t"), CANDIDATES, LLMTask.CATEGORISE_TXN
    )
    assert res.confidence == Decimal("0.60") and res.label == "6900"


def test_unknown_bank_line_goes_to_confirm_queue(books: Any, tiny: Any) -> None:
    """T-BKS-04: an unmatched, rule-less line becomes a CATEGORY confirm item."""
    books.load_bundle(tiny)
    txn = BankTxnOut(
        id="txn_x",
        account_id="acc_operating",
        date=date(2026, 10, 10),
        amount_cents=-4500,
        description="CORNER HARDWARE",
        counterparty=None,
        kind="seed",
        payment_ref=None,
        balance_after_cents=0,
    )
    books.ingest_bank([txn])
    books.categorise_pending(None)
    assert books.post_ready() == 0
    queue = books.confirm_queue()
    assert [i.kind for i in queue] == [ConfirmKind.CATEGORY]
    row = books.repo.raw(queue[0].ref_id)
    assert row.status == RawStatus.PENDING_CONFIRMATION
