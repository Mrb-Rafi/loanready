"""LLM classifier (PRD/09 §7, AGT-15): implements ``contracts.services.Classifier``.

The model may only pick one label from the candidates; code caps its confidence at 0.95 and
the owner-confirmation threshold still applies (CTL-FR-06).
"""

from __future__ import annotations

import re
from decimal import Decimal

from agent.masking import EMAIL_RE
from agent.prompts import classify_schema
from contracts.dto_agent import LLMRequest
from contracts.dto_books import CategorisationResult
from contracts.enums import CategorisationMethod, DataClass, LLMTask
from contracts.errors import InvalidParams
from contracts.services import LLMClient
from contracts.untrusted import Untrusted

CONFIDENCE_CAP = Decimal("0.95")
DEFAULT_THRESHOLD = Decimal("0.80")
FALLBACK_LABEL = "6900"
PERSONAL = "PERSONAL"
MERCHANT_MAX_CHARS = 80
_DIGITS = re.compile(r"\d+")


def merchant_string(text: Untrusted) -> Untrusted:
    """Digits and emails masked, whitespace collapsed, truncated: the merchant-name-only
    string the classify task may send (effectively C2, PRD/09 §3)."""
    cleaned = EMAIL_RE.sub("{EMAIL}", str(text))
    cleaned = _DIGITS.sub("{NUM}", cleaned)
    cleaned = " ".join(cleaned.split())[:MERCHANT_MAX_CHARS]
    return Untrusted(cleaned, text.source, text.source_ref)


class LLMClassifier:
    def __init__(self, llm: LLMClient, threshold: Decimal | None = None) -> None:
        self._llm = llm
        self._threshold = threshold if threshold is not None else _default_threshold()

    def classify(
        self, text: Untrusted, candidates: list[str], task: LLMTask
    ) -> CategorisationResult:
        if not isinstance(text, Untrusted):
            raise TypeError("classify expects an Untrusted value")
        if not candidates:
            raise InvalidParams("classify needs at least one candidate")
        if task not in (LLMTask.CATEGORISE_TXN, LLMTask.PERSONAL_VS_BUSINESS):
            raise InvalidParams(f"{task.value} is not a classification task")
        req = LLMRequest(
            task=task,
            payload={"candidate_count": str(len(candidates))},
            data_class=DataClass.C2_COUNTERPARTY,
            output_schema=classify_schema(candidates),
            untrusted_blocks=[merchant_string(text)],
        )
        output = self._llm.complete(req).output
        label = str(output.get("label", ""))
        if label not in candidates:  # schema enforces this; checked again in code (BKS-06)
            return CategorisationResult(
                raw_txn_id=text.source_ref,
                label=FALLBACK_LABEL,
                confidence=Decimal("0"),
                method=CategorisationMethod.FALLBACK,
                needs_confirmation=True,
                rule_id=None,
            )
        confidence = min(
            max(Decimal(str(output.get("confidence", 0))), Decimal("0")), CONFIDENCE_CAP
        )
        return CategorisationResult(
            raw_txn_id=text.source_ref,
            label=label,
            confidence=confidence,
            method=CategorisationMethod.LLM,
            needs_confirmation=confidence < self._threshold or label == PERSONAL,
            rule_id=None,
        )


def _default_threshold() -> Decimal:
    try:
        from infra.settings import get_settings

        return Decimal(str(get_settings().confidence_confirm_threshold))
    except Exception:  # pragma: no cover - settings unavailable
        return DEFAULT_THRESHOLD
