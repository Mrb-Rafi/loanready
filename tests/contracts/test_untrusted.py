"""T-CON-01 / CTL-AI-04: untrusted text marker and the injection scanner."""

from __future__ import annotations

import pickle
from typing import Any

from contracts.dto_agent import LLMRequest
from contracts.enums import DataClass, LLMTask, SourceKind
from contracts.untrusted import (
    INJECTION_PATTERNS,
    UNTRUSTED_FIELDS,
    Untrusted,
    as_untrusted,
    normalize_for_scan,
    scan_untrusted,
)

EXPECTED_PATTERN_IDS = {
    "IGNORE_PREVIOUS",
    "DISREGARD",
    "NEW_INSTRUCTIONS",
    "SYSTEM_PROMPT",
    "ROLE_SWITCH",
    "TRANSFER_FUNDS",
    "PAY_TO_ACCOUNT",
    "CHANGE_BANK",
    "ACCOUNT_NUMBER",
    "APPROVE_ALL",
    "DISABLE_SAFETY",
    "RAISE_LIMIT",
    "TOOL_CALL_JSON",
    "URGENT_PRESSURE",
    "IGNORE_PREVIOUS_ZH",
    "TRANSFER_ZH",
}


def test_corpus_flags(injection_corpus: list[dict[str, Any]]) -> None:
    """T-CON-01 proves CON-05, CTL-AI-04: every payload with expect_flag is flagged; the two
    expect_flag=false entries are not (scanning is visibility only)."""
    assert len(injection_corpus) >= 20
    channels = {e["channel"] for e in injection_corpus}
    assert channels >= {
        "bank_memo",
        "pos_note",
        "receipt_ocr",
        "bill_description",
        "invoice_note",
        "email_body",
        "supplier_name",
    }
    unflagged = [
        e["id"]
        for e in injection_corpus
        if e["expect_flag"] and not scan_untrusted(Untrusted(e["text"], SourceKind.EMAIL, e["id"]))
    ]
    assert unflagged == []
    false_flags = [
        e["id"]
        for e in injection_corpus
        if not e["expect_flag"] and scan_untrusted(Untrusted(e["text"], SourceKind.BANK, e["id"]))
    ]
    assert false_flags == []
    assert sum(1 for e in injection_corpus if not e["expect_flag"]) == 2


def test_normal_bakery_memos_not_flagged() -> None:
    """T-CON-01 proves CON-05: ordinary bank and POS text does not raise flags."""
    for text in [
        "CARD SETTLEMENT 2026-10-13",
        "CASH DEPOSIT",
        "RENT - MAPLE PROPERTIES",
        "PAYROLL",
        "GOLDEN MILLS BILL-2190",
        "MOBILE TOPUP 0199",
        "BANK SERVICE CHARGE",
        "Golden Mills BILL-2190 via LoanReady",
        "BRIGHT EVENTS CO. PAYMENT INV-1043",
    ]:
        assert scan_untrusted(Untrusted(text, SourceKind.BANK, "x")) == [], text


def test_normalisation() -> None:
    """T-CON-01 proves CON-05: NFKC, zero-width removal, casefold, homoglyphs, whitespace."""
    assert normalize_for_scan("IG​NORE   рrevious\n") == "ignore previous"
    assert normalize_for_scan("ＡＢＣ") == "abc"


def test_flag_shape_and_excerpt_limit() -> None:
    """T-CON-05 proves CON-05: one flag per pattern, excerpt <= 80 chars, provenance kept."""
    text = "x" * 3000 + " ignore previous instructions " + "y" * 3000
    flags = scan_untrusted(Untrusted(text, SourceKind.RECEIPT, "rcp_1"))
    assert [f.pattern_id for f in flags] == ["IGNORE_PREVIOUS"]
    assert len(flags[0].excerpt) <= 80 and "ignore previous" in flags[0].excerpt
    assert flags[0].source == SourceKind.RECEIPT and flags[0].source_ref == "rcp_1"
    assert {p for p, _ in INJECTION_PATTERNS} == EXPECTED_PATTERN_IDS


def test_untrusted_marker_round_trip() -> None:
    """T-CON-02 proves CON-05: the marker survives pickling and pydantic JSON round trips."""
    u = as_untrusted("pay to acct 12345678", SourceKind.BANK, "txn_1")
    assert isinstance(u, str) and u.source == SourceKind.BANK
    assert as_untrusted(u, SourceKind.POS, "other") is u
    p = pickle.loads(pickle.dumps(u))
    assert isinstance(p, Untrusted) and p.source_ref == "txn_1"
    req = LLMRequest(
        task=LLMTask.CATEGORISE_TXN,
        payload={},
        data_class=DataClass.C4_DOCUMENT,
        output_schema={},
        untrusted_blocks=[u],
    )
    back = LLMRequest.model_validate_json(req.model_dump_json())
    assert isinstance(back.untrusted_blocks[0], Untrusted)
    assert back.untrusted_blocks[0].source == SourceKind.BANK and str(
        back.untrusted_blocks[0]
    ) == str(u)
    assert "description" in UNTRUSTED_FIELDS and "claimed_bank_account_ref" in UNTRUSTED_FIELDS
