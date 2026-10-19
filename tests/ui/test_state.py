"""Facade selection and cached reads (PRD/12 §1 state.py, UI-12)."""

from __future__ import annotations

import pytest

from contracts.testing.fakes import FakeAppFacade
from ui import state


def test_fake_when_flag_set() -> None:
    """T-UI-01 proves PRD/12 §1: LOANREADY_UI_FAKE=1 selects FakeAppFacade."""
    app, fake = state.build_facade()
    assert fake and isinstance(app, FakeAppFacade)


def test_fake_when_orchestrator_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-UI-01 proves PC_GUIDE §4 WP10: no orchestrator.container -> FakeAppFacade."""
    monkeypatch.setenv("LOANREADY_UI_FAKE", "0")
    import importlib.util

    if importlib.util.find_spec("orchestrator.container") is not None:
        pytest.skip("orchestrator.container is present on this tree")
    app, fake = state.build_facade()
    assert fake and isinstance(app, FakeAppFacade)


def test_cached_reads_follow_data_version() -> None:
    """T-UI-01 proves UI-12: reads are cached until the facade's data_version changes."""
    app = FakeAppFacade()
    first = state.cached(app, "pending")
    assert first == []
    app.propose_supplier_payment("bil_tiny_2")  # bumps data_version
    assert len(state.cached(app, "pending")) == 1
    with pytest.raises(ValueError):
        state.cached(app, "set_kill_switch", True)
