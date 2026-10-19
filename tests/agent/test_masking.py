"""Masking before remote calls (CTL-AI-03): T-AGT-02."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.masking import Masker, assert_no_identifiers
from contracts.enums import CounterpartyKind
from contracts.errors import RemoteNotAllowed

NAMES = {
    "Golden Mills": CounterpartyKind.SUPPLIER,
    "Golden Mills Wholesale": CounterpartyKind.SUPPLIER,
    "Cafe Aurora": CounterpartyKind.CUSTOMER,
    "Blue Door Deli": CounterpartyKind.CUSTOMER,
}


def _masker() -> Masker:
    return Masker(NAMES, business_name="Tiny Bakery")


def test_stable_numbering_by_sorted_name() -> None:
    """T-AGT-02 proves AGT-04: placeholders are numbered by sorted name per kind."""
    m = _masker()
    assert m.placeholder_for("Blue Door Deli") == "{CUSTOMER_1}"
    assert m.placeholder_for("Cafe Aurora") == "{CUSTOMER_2}"
    assert m.placeholder_for("Golden Mills") == "{SUPPLIER_1}"
    assert m.placeholder_for("Golden Mills Wholesale") == "{SUPPLIER_2}"


def test_longest_name_first_and_case_insensitive() -> None:
    """T-AGT-02 proves AGT-04: longest name wins; matching ignores case."""
    m = _masker()
    out = m.mask("Bill from golden mills wholesale and GOLDEN MILLS for Tiny Bakery")
    assert out == "Bill from {SUPPLIER_2} and {SUPPLIER_1} for {BUSINESS}"


def test_identifiers_removed() -> None:
    """T-AGT-02 proves AGT-04: digit runs >= 6, emails and phones are removed."""
    m = _masker()
    out = m.mask("Acct SBX-GM-77100938, mail ap@goldenmills.test, call +999 555 0100, inv 12345")
    assert "77100938" not in out and "{NUM}" in out
    assert "{EMAIL}" in out and "goldenmills.test" not in out
    assert "{PHONE}" in out and "555" not in out
    assert "12345" in out  # five digits are kept (invoice-style numbers)


def test_amounts_and_dates_survive_masking() -> None:
    """T-AGT-02 proves AGT-04: preformatted amounts and dates are not mangled."""
    m = _masker()
    text = "Invoice INV-1043 for $4,800.00 was due on 14 Oct 2026 (2026-10-14)."
    assert m.mask(text) == text


def test_round_trip_examples() -> None:
    """T-AGT-02 proves AGT-04: unmask(mask(x)) == x for text without removed identifiers."""
    m = _masker()
    text = "Hello Cafe Aurora, Golden Mills Wholesale and Golden Mills say hi from Tiny Bakery."
    assert m.unmask(m.mask(text)) == text


def test_unmask_restores_registered_spelling() -> None:
    """T-AGT-02 proves AGT-04: a differently-cased name unmasks to its registered spelling."""
    m = _masker()
    assert m.unmask(m.mask("hello cafe aurora")) == "hello Cafe Aurora"


_SAFE_ALPHABET = st.sampled_from(list("abcdefghij klmnopqrstuvwxyz.,!?-"))


@settings(max_examples=100, deadline=None, derandomize=True)
@given(
    parts=st.lists(
        st.one_of(
            st.text(_SAFE_ALPHABET, max_size=12),
            st.sampled_from([*NAMES, "Tiny Bakery"]),
        ),
        max_size=8,
    )
)
def test_round_trip_property(parts: list[str]) -> None:
    """T-AGT-02 proves AGT-04: round trip holds for any mix of names and plain words."""
    m = _masker()
    text = " ".join(parts)
    assert m.unmask(m.mask(text)) == text


@pytest.mark.parametrize(
    "text",
    [
        "send to 99887766554433",
        "iban GB82WEST1234AB56987X65",
        "SBX-GM-77100938",
        "mail ap@goldenmills.test",
        "call +999 555 0100",
    ],
)
def test_residual_identifiers_refused(text: str) -> None:
    """T-AGT-02 proves AGT-05: residual account/email/phone patterns refuse the call."""
    with pytest.raises(RemoteNotAllowed):
        assert_no_identifiers(text)


def test_masked_text_passes_residual_check() -> None:
    """T-AGT-02 proves AGT-05: ordinary masked text passes."""
    m = _masker()
    assert_no_identifiers(m.mask("Golden Mills, acct 99887766554433, ap@goldenmills.test"))
