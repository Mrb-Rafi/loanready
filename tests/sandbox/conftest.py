"""WP2 sandbox fixtures: an httpx client on the in-process app, reset to the tiny bundle
before every test, plus token header helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
import pytest

from contracts.testing.builders import load_tiny_bundle
from infra.sandbox_runner import asgi_transport
from sandbox.app import app
from sandbox.ratelimit import LIMITER
from sandbox.settings import get_settings, reset_settings

ACC = "acc_operating"
PAYEE = "pye_tiny_mills"
GOOD_REF = "SBX-TM-00000001"


@dataclass
class Sbx:
    client: httpx.Client

    @property
    def read(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {get_settings().read_token}"}

    @property
    def write(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {get_settings().write_token}"}

    @property
    def admin(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {get_settings().admin_token}"}

    def w(self, key: str, **extra: str) -> dict[str, str]:
        return {**self.write, "Idempotency-Key": key, **extra}

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.client.get(path, headers=kw.pop("headers", self.read), **kw)

    def pay(self, key: str, **body: Any) -> httpx.Response:
        payload = {
            "from_account_id": ACC,
            "payee_id": PAYEE,
            "bank_account_ref": GOOD_REF,
            "amount_cents": 10000,
            "value_date": "2026-10-14",
            "reference": "BILL-1 via LoanReady",
        }
        payload.update(body)
        return self.client.post("/bank/payments/schedule", json=payload, headers=self.w(key))

    def balance(self) -> dict[str, Any]:
        return self.get(f"/bank/accounts/{ACC}/balance").json()

    def ops(self) -> list[list[str]]:
        import csv

        text = self.client.get("/admin/opslog.csv", headers=self.admin).text
        return list(csv.reader(text.splitlines()))[1:]

    def clock(self, d: date) -> httpx.Response:
        return self.client.post("/admin/clock", json={"today": d.isoformat()}, headers=self.admin)


@pytest.fixture
def sbx() -> Iterator[Sbx]:
    reset_settings()
    transport = asgi_transport(app)
    client = httpx.Client(transport=transport, base_url="http://sandbox.local")
    s = Sbx(client)
    r = client.post(
        "/admin/reset", json=load_tiny_bundle().model_dump(mode="json"), headers=s.admin
    )
    assert r.status_code == 200, r.text
    LIMITER.reset()
    yield s
    client.close()
    transport.close()
