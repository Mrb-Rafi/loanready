"""WP6 fixtures: a realistic LoanPackContent (captured from FakeAppFacade), facts, and a
fresh ``Outputs`` on a temp app.db."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from contracts.dto_outputs import LoanPackContent


def pack_content() -> LoanPackContent:
    """The content FakeAppFacade assembles for a 5,000.00 / 60-day request."""
    from contracts.testing.builders import make_loan_pack
    from contracts.testing.fakes import FakeAppFacade

    facade = FakeAppFacade()
    captured: list[LoanPackContent] = []

    class Capture:
        def build_loan_pack(self, content: LoanPackContent) -> Any:
            captured.append(content)
            return make_loan_pack()

        def __getattr__(self, name: str) -> Any:
            return getattr(facade.__dict__["_real_outputs"], name)

    facade.__dict__["_real_outputs"] = facade.outputs
    facade.outputs = Capture()  # type: ignore[assignment]
    facade.build_loan_pack(500000, 60, "Festival stock")
    return captured[0]


@pytest.fixture(scope="session")
def content(_test_environment: Any) -> LoanPackContent:
    return pack_content()


@pytest.fixture
def outputs(app_db: Any, clock: Any, ids: Any, settings: Any, tmp_path: Any) -> Any:
    from outputs.service import Outputs

    return Outputs(app_db, clock, ids, settings, business_name="Tiny Bakery", packs_dir=tmp_path)


@pytest.fixture
def invoice() -> Any:
    from contracts.testing.builders import make_invoice

    return make_invoice()  # INV-1, 600.00, due 2026-10-09


@pytest.fixture
def customer() -> Any:
    from contracts.testing.builders import make_counterparty

    return make_counterparty()


AS_OF = date(2026, 10, 14)
