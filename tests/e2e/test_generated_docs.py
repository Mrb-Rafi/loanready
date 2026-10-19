"""T-E2E-03 (PRD/13 §5): the generated tier table and compliance matrix contain every
ActionType and every CTL id; THIRD_PARTY.md lists every locked package."""

from __future__ import annotations

from collections import Counter
from typing import Any

import pytest

from contracts.enums import ActionType
from contracts.registry import CONTROL_IDS

pytestmark = [pytest.mark.e2e]


def test_tier_table_lists_every_action_type(app_facade: Any) -> None:
    """T-E2E-03 proves PRD/13 §1, CTL-AC-01, CTL-AC-02: every ActionType appears with its
    tier."""
    from scripts.dump_tiers import render

    text = render(app_facade.tier_table())
    for a in ActionType:
        assert f"`{a.value}`" in text, a


def test_compliance_matrix_lists_every_control(app_facade: Any) -> None:
    """T-E2E-03 proves PRD/13 §1, GOV-19, PRD/14 §4: every CTL id appears; the preface says
    alignment, never compliance."""
    from scripts.dump_controls import PREFACE, render

    text = render(app_facade.controls_table(), {}, Counter(), False)
    for cid in CONTROL_IDS:
        assert f"| {cid} |" in text, cid
    assert "designed around" in PREFACE and "not** a certification" in PREFACE


def test_third_party_lists_every_locked_package() -> None:
    """T-E2E-03 proves PRD/13 §2: every requirements.lock entry has a licence row."""
    from scripts.licenses import lock_entries, render

    text = render()
    for name, version, _marker in lock_entries():
        assert f"| {name} | {version} |" in text, name


@pytest.fixture
def app_facade() -> Any:
    from contracts.testing.fakes import FakeAppFacade

    return FakeAppFacade()


def test_docs_cite_existing_tests() -> None:
    """T-E2E-03 proves PRD/13 §2, PRD/14 §4: every test path the hand-written documents
    cite exists, so the security self-assessment never points at missing evidence."""
    import re

    from infra.paths import REPO_ROOT

    missing = []
    for doc in ("docs/security_self_assessment.md", "docs/technical_doc.md", "README.md"):
        text = (REPO_ROOT / doc).read_text(encoding="utf-8")
        for ref in re.findall(r"`(tests/[^`*]+?\.py)`", text):
            if not (REPO_ROOT / ref).exists():
                missing.append(f"{doc}: {ref}")
    assert missing == []
