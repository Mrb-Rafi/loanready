"""T-DGN-05 proves REG-DP-05, CTL-DQ-02: synthetic identifiers only (``.test`` emails,
``+999`` phones, ``SBX-`` bank accounts)."""

from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d{6,15}")


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def test_every_email_is_dot_test(bundle: Any) -> None:
    """T-DGN-05: every email anywhere in the bundle ends with .test."""
    emails = [m for s in _strings(bundle.model_dump(mode="json")) for m in EMAIL_RE.findall(s)]
    assert emails
    assert all(e.endswith(".test") for e in emails), emails


def test_every_phone_is_plus_999(bundle: Any) -> None:
    """T-DGN-05: every phone number anywhere in the bundle is in the +999 range."""
    phones = [m for s in _strings(bundle.model_dump(mode="json")) for m in PHONE_RE.findall(s)]
    assert phones
    assert all(p.startswith("+999") for p in phones), phones


def test_contacts_are_email_or_phone(bundle: Any) -> None:
    """T-DGN-05: each contact_ref is a .test email or a +999 phone."""
    for c in bundle.counterparties:
        if c.contact_ref is None:
            continue
        assert c.contact_ref.endswith(".test") or c.contact_ref.startswith("+999"), c.id


def test_bank_accounts_are_sbx(bundle: Any) -> None:
    """T-DGN-05: payee accounts and claimed accounts start with SBX-."""
    refs = [p.bank_account_ref for p in bundle.payees + bundle.bank_payees]
    refs += [b.claimed_bank_account_ref for b in bundle.bills if b.claimed_bank_account_ref]
    assert refs and all(r.startswith("SBX-") for r in refs), refs


def test_business_is_fictional(bundle: Any) -> None:
    """T-DGN-05: the fictional profile of PRD/10 §2."""
    assert bundle.business["name"] == "Rina's Bakery"
    assert bundle.business["city"] == "Riverton"
