"""Port adapters (WP2): BankPort, PosPort, MessagePort, LenderPort and SandboxAdminPort over
HTTP to the sandbox. Every write verifies its ExecutionTicket before a request is sent
(PRD/04 §10). Adapters never import ``sandbox``; in-process mode gets an ASGI transport
from ``infra.sandbox_runner`` through ``build_ports``."""

from __future__ import annotations

from typing import Any

import httpx

from adapters.admin import HttpSandboxAdminPort
from adapters.bank import HttpBankPort
from adapters.http import INPROCESS_BASE_URL, ReadClient, WriteClient
from adapters.lender import HttpLenderPort
from adapters.messaging import HttpMessagePort
from adapters.pos import HttpPosPort
from contracts.ports import Ports, TicketVerifier

REQUIRES_CONTRACTS = "1"


def build_ports(
    settings: Any, verifier: TicketVerifier, transport: httpx.BaseTransport | None = None
) -> Ports:
    """Ports for ``SANDBOX_MODE`` (in-process when a transport is given, else ``SANDBOX_URL``)."""
    base = INPROCESS_BASE_URL if transport is not None else settings.sandbox_url
    read = ReadClient(base, settings.sandbox_read_token.get_secret_value(), transport)
    write = WriteClient(base, settings, transport)
    admin = ReadClient(base, settings.sandbox_admin_token.get_secret_value(), transport)
    return Ports(
        bank=HttpBankPort(read, write, verifier),
        pos=HttpPosPort(read),
        messaging=HttpMessagePort(read, write, verifier),
        lender=HttpLenderPort(read, write, verifier),
        admin=HttpSandboxAdminPort(admin),
    )


__all__ = ["REQUIRES_CONTRACTS", "Ports", "build_ports"]
