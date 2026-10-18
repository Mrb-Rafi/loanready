"""Bearer tokens with scopes read | write | admin (SBX-01, SBX-02; CTL-AC-05).

Tokens are compared in constant time and never appear in responses or logs: the ops log
stores only the scope name."""

from __future__ import annotations

import hmac
from collections.abc import Callable

from fastapi import Request

from sandbox.errors import SandboxHTTPError
from sandbox.settings import get_settings

SCOPES: dict[str, frozenset[str]] = {
    "read": frozenset({"read"}),
    "write": frozenset({"read", "write"}),
    "admin": frozenset({"read", "write", "admin"}),
}


def token_name(authorization: str | None) -> str | None:
    """Which token (read/write/admin) the header carries, or None."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    presented = authorization[7:].strip().encode("utf-8")
    s = get_settings()
    found = None
    for name, token in (("read", s.read_token), ("write", s.write_token), ("admin", s.admin_token)):
        if hmac.compare_digest(presented, token.encode("utf-8")):
            found = name
    return found


def require(scope: str) -> Callable[[Request], str]:
    """FastAPI dependency: 401 without a valid token, 403 when the scope is missing."""

    def dependency(request: Request) -> str:
        name = token_name(request.headers.get("authorization"))
        if name is None:
            raise SandboxHTTPError("UNAUTHENTICATED", "missing or invalid bearer token")
        request.state.sbx_scope = name
        if scope not in SCOPES[name]:
            raise SandboxHTTPError("INSUFFICIENT_SCOPE", f"this endpoint needs the {scope} scope")
        return name

    return dependency
