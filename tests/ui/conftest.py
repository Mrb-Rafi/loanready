"""UI test setup: FakeAppFacade (LOANREADY_UI_FAKE=1) and fresh Streamlit caches per test."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

APP_FILE = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")
TIMEOUT = 60


@pytest.fixture(autouse=True)
def _ui_fake(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("LOANREADY_UI_FAKE", "1")
    from ui import state

    state.clear_caches()
    yield
    state.clear_caches()
