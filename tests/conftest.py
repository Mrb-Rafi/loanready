"""Shared fixtures (WP0, frozen). Every test runs with ``settings_for_tests()``: test mode,
mock LLM, sidecar OCR, in-process sandbox, FixedClock at 2026-10-14, seeded IDs, and a
fresh ``var/`` per test session (never the developer's ``.env`` or ``var/``)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

from contracts.testing.settings import TEST_SEED, settings_for_tests

_SESSION_ENV: dict[str, str] = {}


@pytest.fixture(scope="session", autouse=True)
def _test_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    var = tmp_path_factory.mktemp("var")
    env = settings_for_tests(var)
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    _SESSION_ENV.update(env)
    from infra.db import dispose_all
    from infra.settings import reset_settings

    reset_settings()
    yield env
    dispose_all()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_settings()


@pytest.fixture(autouse=True)
def _fresh_singletons(_test_environment: dict[str, str]) -> Iterator[None]:
    """Each test starts with a fresh FixedClock and a freshly seeded ID factory."""
    from contracts.clock import FixedClock, set_clock
    from contracts.ids import IdFactory, set_id_factory
    from contracts.testing.builders import AS_OF

    set_clock(FixedClock(AS_OF))
    set_id_factory(IdFactory(TEST_SEED))
    yield


@pytest.fixture
def settings(_test_environment: dict[str, str]) -> Any:
    from infra.settings import get_settings

    return get_settings()


@pytest.fixture
def clock() -> Any:
    from contracts.clock import get_clock

    return get_clock()


@pytest.fixture
def ids() -> Any:
    from contracts.ids import get_id_factory

    return get_id_factory()


@pytest.fixture
def app_db(tmp_path: Any) -> Iterator[Any]:
    """A fresh SQLite app database with every contracts.schema table."""
    from infra.db import create_all, engine_for

    eng = engine_for(tmp_path / "app.db")
    create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def tiny() -> Any:
    """``tiny_business.json`` as a SeedBundle (PRD/15 §3.1)."""
    from contracts.testing.builders import load_tiny_bundle

    return load_tiny_bundle()


@pytest.fixture
def injection_corpus() -> list[dict[str, Any]]:
    from contracts.testing import INJECTION_CORPUS_PATH

    return json.loads(INJECTION_CORPUS_PATH.read_text(encoding="utf-8"))["entries"]


@pytest.fixture
def sandbox_client() -> Iterator[Any]:
    """httpx client on the in-process sandbox app; skips cleanly if WP2 is absent."""
    import importlib.util

    if (
        importlib.util.find_spec("sandbox") is None
        or importlib.util.find_spec("sandbox.app") is None
    ):
        pytest.skip("sandbox package (WP2) not present")
    import httpx

    from infra.sandbox_runner import asgi_transport

    transport = asgi_transport()
    with httpx.Client(transport=transport, base_url="http://sandbox.local") as client:
        yield client
    transport.close()


@pytest.fixture
def owner() -> Any:
    from contracts.actions import OWNER

    return OWNER


@pytest.fixture
def bookkeeper() -> Any:
    from contracts.actions import BOOKKEEPER

    return BOOKKEEPER


@pytest.fixture
def agent_principal() -> Any:
    from contracts.actions import AGENT

    return AGENT
