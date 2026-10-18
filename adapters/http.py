"""HTTP clients for the sandbox (SBX-A-01, SBX-A-02, SBX-A-05).

``ReadClient`` uses the read token and follows ``X-Next-Cursor`` pagination. ``WriteClient``
uses the write token and is called only after the execution ticket has been verified; it
sends ``Idempotency-Key = ticket.action_id`` and ``X-Ticket-Id``. Timeouts 10 s; one retry
only on connection errors (same idempotency key), never on an HTTP response."""

from __future__ import annotations

from typing import Any

import httpx

from adapters._secrets import write_token
from adapters.errors import raise_for_error
from contracts.actions import ExecutionTicket
from contracts.sandbox_api import HEADER_IDEMPOTENCY_KEY, HEADER_NEXT_CURSOR, HEADER_TICKET_ID

TIMEOUT_S = 10.0
INPROCESS_BASE_URL = "http://sandbox.local"


def _client(base_url: str, token: str, transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        transport=transport,
        timeout=TIMEOUT_S,
        headers={"Authorization": f"Bearer {token}"},
    )


def _send(client: httpx.Client, method: str, path: str, **kw: Any) -> httpx.Response:
    try:
        return client.request(method, path, **kw)
    except httpx.TransportError:
        return client.request(method, path, **kw)  # one retry, connection errors only


class ReadClient:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self._client = _client(base_url, token, transport)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = _send(self._client, "GET", path, params=params)
        raise_for_error(resp)
        return resp.json()

    def get_text(self, path: str) -> str:
        resp = _send(self._client, "GET", path)
        raise_for_error(resp)
        return resp.text

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> list[Any]:
        """Follow X-Next-Cursor until exhausted."""
        params = dict(params or {})
        out: list[Any] = []
        while True:
            resp = _send(self._client, "GET", path, params=params)
            raise_for_error(resp)
            out.extend(resp.json())
            cursor = resp.headers.get(HEADER_NEXT_CURSOR)
            if not cursor:
                return out
            params["cursor"] = cursor

    def post(self, path: str, json: Any) -> Any:
        """Admin writes only (reset, payees, clock); no ticket involved."""
        resp = _send(self._client, "POST", path, json=json)
        raise_for_error(resp)
        return resp.json()

    def close(self) -> None:
        self._client.close()


class WriteClient:
    def __init__(self, base_url: str, settings: Any, transport: httpx.BaseTransport | None = None):
        self._client = _client(base_url, write_token(settings), transport)

    def post(self, path: str, ticket: ExecutionTicket, json: Any | None = None) -> Any:
        headers = {HEADER_IDEMPOTENCY_KEY: ticket.action_id, HEADER_TICKET_ID: ticket.ticket_id}
        resp = _send(self._client, "POST", path, json=json, headers=headers)
        raise_for_error(resp)
        return resp.json()

    def close(self) -> None:
        self._client.close()
