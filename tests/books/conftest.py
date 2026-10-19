"""WP3 fixtures: a fresh ``Books`` on a temp app.db, the tiny business fully synced, and
(when WP8 is present) the seeded Rina's Bakery bundle."""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest


@pytest.fixture
def books(app_db: Any, clock: Any, ids: Any, settings: Any) -> Any:
    from books.service import Books

    return Books(app_db, clock, ids, settings)


def sync(b: Any, bundle: Any) -> None:
    """What the orchestrator's seed + sync_all does (PRD/11 §7)."""
    b.load_bundle(bundle)
    b.ingest_pos(bundle.pos_sales)
    b.ingest_bank(bundle.bank_transactions)
    b.categorise_pending(None)
    b.post_ready()


@pytest.fixture
def tiny_books(books: Any, tiny: Any) -> Any:
    sync(books, tiny)
    return books


@pytest.fixture(scope="session")
def rina_bundle(_test_environment: Any) -> Any:
    if importlib.util.find_spec("datagen") is None:
        pytest.skip("datagen (WP8) not present")
    from datagen.generate import tune

    return tune().bundle


@pytest.fixture(scope="session")
def rina_books(rina_bundle: Any, tmp_path_factory: pytest.TempPathFactory) -> Any:
    """Books loaded with the seeded bakery and synced once per session."""
    from books.service import Books
    from contracts.clock import FixedClock
    from contracts.ids import IdFactory
    from contracts.testing.settings import TEST_SEED
    from infra.db import create_all, engine_for
    from infra.settings import get_settings

    eng = engine_for(tmp_path_factory.mktemp("rina") / "app.db")
    create_all(eng)
    b = Books(eng, FixedClock(rina_bundle.as_of), IdFactory(TEST_SEED), get_settings())
    sync(b, rina_bundle)
    return b
