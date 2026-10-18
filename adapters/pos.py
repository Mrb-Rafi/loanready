"""HttpPosPort (PosPort over HTTP, read only)."""

from __future__ import annotations

from datetime import date

from adapters.http import ReadClient
from contracts.sandbox_api import PosSaleOut


class HttpPosPort:
    def __init__(self, read: ReadClient):
        self._read = read

    def list_sales(self, start: date, end: date) -> list[PosSaleOut]:
        rows = self._read.get_all(
            "/pos/sales", {"start": start.isoformat(), "end": end.isoformat()}
        )
        return [PosSaleOut.model_validate(r) for r in rows]
