"""HttpSandboxAdminPort (admin token). Used only by ``orchestrator.seed`` and tests."""

from __future__ import annotations

from datetime import date
from typing import Any

from adapters.http import ReadClient
from contracts.seed import SeedBundle


class HttpSandboxAdminPort:
    def __init__(self, admin: ReadClient):
        self._admin = admin

    def reset(self, bundle: SeedBundle) -> dict[str, int]:
        return self._admin.post("/admin/reset", bundle.model_dump(mode="json"))

    def health(self) -> dict[str, Any]:
        return self._admin.get("/health")

    def get_clock(self) -> date:
        return date.fromisoformat(self._admin.get("/admin/clock")["today"])

    def set_clock(self, today: date) -> date:
        return date.fromisoformat(
            self._admin.post("/admin/clock", {"today": today.isoformat()})["today"]
        )

    def opslog_csv(self) -> str:
        return self._admin.get_text("/admin/opslog.csv")

    def opslog_verify(self) -> dict[str, int | None]:
        return self._admin.get("/admin/opslog/verify")
