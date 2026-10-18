"""WP9 integration fixtures (PRD/15 §4.9).

``app`` is the real composition: governance (WP1), adapters over the in-process sandbox
(WP2) and every other package that is assembled; any package still missing is replaced by
its fake (``fakes="auto"``). The sandbox and the books are seeded from the tiny fixture
plus the extra bills a test asks for. Tests that need every package carry ``@real`` and
skip cleanly until all hand-offs are assembled.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from contracts.seed import SeedBundle
from tests.integration._support import fraud_bill, tiny_with


@pytest.fixture
def make_app(settings: Any, tmp_path: Any) -> Iterator[Callable[..., Any]]:
    """Factory: ``make_app(bundle=None, fakes="auto")`` -> seeded App on a fresh app DB."""
    from infra.db import engine_for
    from orchestrator.container import build_app
    from orchestrator.seed import seed_app

    apps: list[Any] = []

    def factory(bundle: SeedBundle | None = None, fakes: Any = "auto", seed: bool = True) -> Any:
        if fakes is not True:
            _reset_sandbox()
        app = build_app(settings, fakes=fakes, engine=engine_for(tmp_path / f"app{len(apps)}.db"))
        apps.append(app)
        if seed:
            app.c.extras["seed"] = seed_app(app, bundle or tiny_with())
        return app

    yield factory
    for a in apps:
        a.close()


def _reset_sandbox() -> None:
    if importlib.util.find_spec("sandbox") is None:
        return
    from sandbox.ratelimit import LIMITER
    from sandbox.settings import reset_settings

    reset_settings()
    LIMITER.reset()


@pytest.fixture
def app(make_app: Callable[..., Any]) -> Any:
    """Seeded app with the S6-style fraud bill loaded next to the tiny fixture."""
    if importlib.util.find_spec("sandbox") is None:
        pytest.skip("sandbox (WP2) not present")
    return make_app(tiny_with([fraud_bill()]))


@pytest.fixture
def seeded_real(make_app: Callable[..., Any]) -> Any:
    """Every package real, seeded from the generated bundle (``datagen``); skips until the
    WP3-WP8 hand-offs are assembled."""
    from orchestrator.seed import load_or_generate_bundle
    from tests.integration._support import all_packages_present

    if not all_packages_present():
        pytest.skip("needs every package assembled (WP3-WP8 hand-offs missing)")
    bundle, _source = load_or_generate_bundle(generate=True)
    return make_app(bundle, fakes=False)
