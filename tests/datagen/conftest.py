"""WP8 fixtures: one tuned generation per test session, written to a temp folder."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(scope="session")
def generated() -> Any:
    from datagen.generate import tune

    return tune()


@pytest.fixture(scope="session")
def bundle(generated: Any) -> Any:
    return generated.bundle


@pytest.fixture(scope="session")
def written(generated: Any, tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Path]]:
    from datagen.generate import write_outputs

    root = tmp_path_factory.mktemp("datagen_out")
    yield write_outputs(generated, root / "var", root / "data")
