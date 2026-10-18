"""WP2 adapter fixtures: real ports over the in-process sandbox (reset to the tiny bundle),
with the strict fake ticket verifier from contracts.testing."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from adapters import build_ports
from contracts.ports import Ports
from contracts.testing.builders import load_tiny_bundle
from contracts.testing.fakes import make_simple_verifier
from infra.sandbox_runner import asgi_transport
from sandbox.app import app
from sandbox.ratelimit import LIMITER
from sandbox.settings import reset_settings


@dataclass
class Env:
    ports: Ports
    settings: Any


@pytest.fixture
def env(settings: Any) -> Iterator[Env]:
    reset_settings()
    transport = asgi_transport(app)
    ports = build_ports(settings, make_simple_verifier(), transport)
    ports.admin.reset(load_tiny_bundle())
    LIMITER.reset()
    yield Env(ports, settings)
    transport.close()
