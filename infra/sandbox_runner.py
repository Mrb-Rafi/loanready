"""Sandbox launch helpers.

``SANDBOX_MODE=inprocess``: ``asgi_transport()`` returns a synchronous httpx transport
that serves requests from the sandbox ASGI app in this process (no port). The sandbox is
imported lazily by name, so no app package imports it statically (ARC-05).
``SANDBOX_MODE=external``: ``external_health()`` checks the separate process.
"""

from __future__ import annotations

import importlib
import socket
from typing import Any
from urllib.parse import urlparse

import anyio.from_thread
import httpx

SANDBOX_APP_MODULE = "sandbox.app"


class InProcessTransport(httpx.BaseTransport):
    """Synchronous transport over ``httpx.ASGITransport`` (which is async-only), using a
    long-lived anyio blocking portal."""

    def __init__(self, app: Any):
        self._asgi = httpx.ASGITransport(app=app)
        self._portal_cm = anyio.from_thread.start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        self._closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.read()

        async def call() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
            req = httpx.Request(request.method, request.url, headers=request.headers, content=body)
            resp = await self._asgi.handle_async_request(req)
            content = await resp.aread()
            return resp.status_code, resp.headers.raw, content

        status, headers, content = self._portal.call(call)
        return httpx.Response(status, headers=headers, content=content, request=request)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._portal_cm.__exit__(None, None, None)


def load_sandbox_app() -> Any:
    """Import ``sandbox.app:app`` by name (raises ModuleNotFoundError if WP2 is absent)."""
    return importlib.import_module(SANDBOX_APP_MODULE).app


def asgi_transport(app: Any | None = None) -> InProcessTransport:
    """Transport for ``SANDBOX_MODE=inprocess`` (the sandbox app unless one is given)."""
    return InProcessTransport(app if app is not None else load_sandbox_app())


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def external_health(url: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """GET {url}/health of an external sandbox; None if nothing answers."""
    try:
        r = httpx.get(url.rstrip("/") + "/health", timeout=timeout)
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


def mode_mismatch(mode: str, url: str) -> str | None:
    """Explain an inprocess/external mismatch (UI-16), or None if consistent."""
    parsed = urlparse(url)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8100
    busy = port_in_use(host, port)
    if mode == "inprocess" and busy:
        return (
            f"SANDBOX_MODE=inprocess but something answers on {host}:{port}. Stop the "
            "external sandbox, or set SANDBOX_MODE=external. Never run both on the same var/."
        )
    if mode == "external" and not busy:
        return (
            f"SANDBOX_MODE=external but nothing answers on {host}:{port}. Start it with "
            "`python tasks.py sandbox`, or set SANDBOX_MODE=inprocess."
        )
    return None
