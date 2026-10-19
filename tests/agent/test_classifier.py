"""LLM classifier (AGT-15): T-AGT-07."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from agent.classifier import LLMClassifier
from agent.llm import LLM
from contracts.enums import CategorisationMethod, LLMBackend, LLMTask, SourceKind
from contracts.errors import InvalidParams
from contracts.services import Classifier
from contracts.untrusted import Untrusted

CANDIDATES = ["5000", "6000", "6100", "6200", "6900", "PERSONAL"]


def _memo(text: str, ref: str = "raw_1") -> Untrusted:
    return Untrusted(text, SourceKind.BANK, ref)


def _ollama(stub: Any, events: Any, answer: str) -> tuple[LLMClassifier, Any]:
    backend = stub(LLMBackend.OLLAMA, [answer, answer])
    llm = LLM(mode="ollama", events=events, backends={LLMBackend.OLLAMA: backend})
    return LLMClassifier(llm), backend


def _remote(stub: Any, events: Any, answer: str) -> tuple[LLMClassifier, Any]:
    """A remote (OpenAI-like) stub: the generic 0.95 cap applies, not the local 0.75 one."""
    backend = stub(LLMBackend.OPENAI, [answer, answer], remote=True)
    llm = LLM(mode="openai", events=events, backends={LLMBackend.OPENAI: backend})
    return LLMClassifier(llm), backend


def test_satisfies_protocol(events: Any) -> None:
    """T-AGT-07 proves AGT-15: LLMClassifier implements contracts.services.Classifier."""
    assert isinstance(LLMClassifier(LLM(mode="mock", events=events)), Classifier)


def test_mock_keyword_match(events: Any) -> None:
    """T-AGT-07 proves AGT-15, CTL-FR-06: mock matches keywords at 0.60 → owner confirms."""
    clf = LLMClassifier(LLM(mode="mock", events=events))
    res = clf.classify(_memo("GOLDEN MILLS FLOUR 0042"), CANDIDATES, LLMTask.CATEGORISE_TXN)
    assert res.label == "5000"
    assert res.confidence == Decimal("0.6")
    assert res.method == CategorisationMethod.LLM
    assert res.needs_confirmation is True
    assert res.raw_txn_id == "raw_1"
    assert res.rule_id is None


def test_mock_no_match_goes_to_owner(events: Any) -> None:
    """T-AGT-07 proves AGT-15: unknown merchants get confidence 0 and need confirmation."""
    clf = LLMClassifier(LLM(mode="mock", events=events))
    res = clf.classify(_memo("ZZZ UNKNOWN SHOP"), CANDIDATES, LLMTask.CATEGORISE_TXN)
    assert res.confidence == Decimal("0")
    assert res.needs_confirmation is True
    assert res.label in CANDIDATES


def test_label_outside_candidates_rejected(events: Any, stub: Any) -> None:
    """T-AGT-07 proves AGT-15, BKS-06: a label outside the candidates never comes back; the
    invalid answer falls back to mock (schema enum) and the result is a candidate."""
    clf, backend = _ollama(stub, events, '{"label": "1000", "confidence": 0.99}')
    res = clf.classify(_memo("ZZZ SHOP"), CANDIDATES, LLMTask.CATEGORISE_TXN)
    assert len(backend.calls) == 2
    assert res.label in CANDIDATES
    assert res.label != "1000"


def test_confidence_capped(events: Any, stub: Any) -> None:
    """T-AGT-07 proves AGT-15: model confidence is capped at 0.95 via Decimal(str(x))."""
    clf, _ = _remote(stub, events, '{"label": "6200", "confidence": 1.0}')
    res = clf.classify(_memo("CITY POWER"), CANDIDATES, LLMTask.CATEGORISE_TXN)
    assert res.label == "6200"
    assert res.confidence == Decimal("0.95")
    assert res.needs_confirmation is False


def test_confidence_from_str_not_float(events: Any, stub: Any) -> None:
    """T-AGT-07 proves AGT-15: 0.85 converts exactly, no binary float artefacts."""
    clf, _ = _remote(stub, events, '{"label": "6000", "confidence": 0.85}')
    res = clf.classify(_memo("RENT OCT"), CANDIDATES, LLMTask.CATEGORISE_TXN)
    assert res.confidence == Decimal("0.85")


def test_personal_always_needs_confirmation(events: Any, stub: Any) -> None:
    """T-AGT-07 proves CTL-FR-06: PERSONAL needs owner confirmation even at high confidence."""
    clf, _ = _ollama(stub, events, '{"label": "PERSONAL", "confidence": 0.9}')
    res = clf.classify(_memo("CINEPLEX"), CANDIDATES, LLMTask.PERSONAL_VS_BUSINESS)
    assert res.label == "PERSONAL" and res.needs_confirmation is True


def test_digits_masked_before_model(events: Any, stub: Any) -> None:
    """T-AGT-07 proves AGT-15, CTL-AI-03: digits are masked from the merchant string before
    the model sees it."""
    clf, backend = _ollama(stub, events, '{"label": "5000", "confidence": 0.7}')
    clf.classify(_memo("GOLDEN MILLS 99887766554433 REF 42"), CANDIDATES, LLMTask.CATEGORISE_TXN)
    prompt = "\n".join(m["content"] for m in backend.calls[0][1])
    block = prompt[prompt.index("<data source=") :]
    assert "99887766554433" not in prompt and "42" not in block
    assert "GOLDEN MILLS" in prompt


def test_empty_candidates_rejected(events: Any) -> None:
    """T-AGT-07 proves AGT-15: an empty candidate list is invalid."""
    with pytest.raises(InvalidParams):
        LLMClassifier(LLM(mode="mock", events=events)).classify(
            _memo("x"), [], LLMTask.CATEGORISE_TXN
        )


def test_plain_str_rejected(events: Any) -> None:
    """T-AGT-07 proves AGT-07: classify accepts only Untrusted text."""
    with pytest.raises(TypeError):
        LLMClassifier(LLM(mode="mock", events=events)).classify(
            "FLOUR",  # type: ignore[arg-type]
            CANDIDATES,
            LLMTask.CATEGORISE_TXN,
        )


def test_local_model_never_auto_posts(stub: Any) -> None:
    """T-AGT-07 proves AGT-15, BKS-06: a local model's confidence is capped below the 0.80
    threshold (it pre-fills the owner's choice); a remote answer keeps the 0.95 cap; the
    label meanings from the chart of accounts are in the prompt facts."""
    import json as _json
    from decimal import Decimal as _D

    from agent.classifier import LLMClassifier
    from agent.llm import LLM
    from contracts.enums import LLMBackend, LLMTask, SourceKind
    from contracts.untrusted import as_untrusted

    sure = _json.dumps({"label": "5000", "confidence": 0.99})
    local = stub(LLMBackend.OLLAMA, [sure])
    clf = LLMClassifier(LLM(mode="ollama", backends={LLMBackend.OLLAMA: local}), _D("0.80"))
    text = as_untrusted("GRAIN DEPOT FLOUR", SourceKind.BANK, "t1")
    r = clf.classify(text, ["5000", "6900"], LLMTask.CATEGORISE_TXN)
    assert r.confidence == _D("0.75") and r.needs_confirmation
    assert "5000 = " in local.calls[0][1][1]["content"]
    remote = stub(LLMBackend.OPENAI, [sure], remote=True)
    clf = LLMClassifier(LLM(mode="openai", backends={LLMBackend.OPENAI: remote}), _D("0.80"))
    r = clf.classify(text, ["5000", "6900"], LLMTask.CATEGORISE_TXN)
    assert r.confidence == _D("0.95") and not r.needs_confirmation
