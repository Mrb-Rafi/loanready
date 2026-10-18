"""LoanReady sandbox service (WP2): MockBank, POS, Messaging and a pretend lender.

``uvicorn sandbox.app:app --host 127.0.0.1 --port 8100`` (``python tasks.py sandbox``), or
in-process through ``infra.sandbox_runner.asgi_transport()``. A small, strict, fake bank:
synthetic data only, reserved fake identifiers, nothing leaves the machine."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from contracts.canonical import file_sha256
from sandbox import admin
from sandbox.bank.router import router as bank_router
from sandbox.clock import today
from sandbox.errors import SandboxHTTPError
from sandbox.lender.router import router as lender_router
from sandbox.messaging.router import router as messaging_router
from sandbox.opslog import OpsLogMiddleware
from sandbox.pos.router import router as pos_router
from sandbox.settings import REPO_ROOT

OPENAPI_PATH = REPO_ROOT / "sandbox" / "openapi.json"
VERSION = "1"

app = FastAPI(
    title="LoanReady Sandbox",
    version=VERSION,
    description="Self-built simulator with scoped tokens, idempotency and hash-chained "
    "operation logs. Synthetic data only.",
)
app.include_router(bank_router)
app.include_router(pos_router)
app.include_router(messaging_router)
app.include_router(lender_router)
app.include_router(admin.router)
app.add_middleware(OpsLogMiddleware)


@app.exception_handler(SandboxHTTPError)
async def _sandbox_error(_request: Request, exc: SandboxHTTPError) -> JSONResponse:
    return exc.response()


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    details = {
        "errors": [
            {"loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg", "")}
            for e in exc.errors()
        ]
    }
    return SandboxHTTPError("VALIDATION_ERROR", "request validation failed", details).response()


@lru_cache(maxsize=1)
def openapi_sha256() -> str:
    """SHA-256 of the committed sandbox/openapi.json (the frozen API, SBX-12)."""
    return file_sha256(OPENAPI_PATH) if OPENAPI_PATH.exists() else "missing"


@app.get("/health", tags=["health"])
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "today": today().isoformat(),
        "version": VERSION,
        "openapi_sha256": openapi_sha256(),
    }
