"""Composition root (PRD/11 §2): ``build_app(settings=None, *, inprocess_sandbox=True,
fakes="auto") -> App``.

This is the only module that names the Layer 2 packages. They are imported **by name**
(``importlib``) so a partly assembled tree still starts: with ``fakes="auto"`` (ORC-01) a
missing package is replaced by its fake from ``contracts.testing.fakes`` and a warning is
logged once; ``fakes=True`` uses fakes everywhere (UI development); ``fakes=False``
requires every package (integration tests and the demo).

Real constructors follow PRD/11 §2:

    Books(engine, clock, ids, settings)      Outputs(engine, clock, ids, settings)
    build_governance(engine, clock, ids, lookup, executor, settings)
    build_ports(settings, gate.ticket_verifier(), transport)
    LLM(settings, events=sink)  Agent(llm, events=sink)
    Planner()  Financing(catalogue_path, regparams_path)  Readiness()
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Engine

from contracts.clock import Clock, get_clock
from contracts.ids import IdFactory, get_id_factory
from contracts.registry import EXPECTED_PACKAGES
from contracts.seed import SeedBundle
from infra import versions
from infra.db import create_all
from infra.db import engine as default_engine
from infra.paths import DATA_DIR, ensure_dirs, var_dir
from infra.settings import Settings, get_settings
from orchestrator.events import EventSink
from orchestrator.executors import ExecutorImpl
from orchestrator.lookup import EntityLookupImpl

log = logging.getLogger("loanready.orchestrator")

FakesMode = bool | Literal["auto"]
# Packages the container composes (the sandbox, ui, scripts and datagen are not services).
COMPOSED = ("governance", "adapters", "books", "planning", "financing", "outputs", "agent")
_WARNED: set[str] = set()


def package_present(pkg: str) -> bool:
    """The package and its entry module exist and export the registry's service names."""
    spec = EXPECTED_PACKAGES[pkg]
    try:
        if importlib.util.find_spec(pkg) is None:
            return False
        if importlib.util.find_spec(spec.entry_module) is None:
            return False
    except (ImportError, ValueError):
        return False
    if not spec.service_names:
        return True
    module = importlib.import_module(spec.entry_module)
    return all(hasattr(module, name) for name in spec.service_names)


def resolve_modes(fakes: FakesMode) -> dict[str, str]:
    """``{package: "real" | "fake"}`` for every composed package (ORC-01)."""
    if fakes is True:
        return dict.fromkeys(COMPOSED, "fake")
    present = {pkg: package_present(pkg) for pkg in COMPOSED}
    missing = [pkg for pkg, ok in present.items() if not ok]
    if fakes is False and missing:
        names = ", ".join(f"{p} ({EXPECTED_PACKAGES[p].wp})" for p in missing)
        raise RuntimeError(f"build_app(fakes=False) needs every package; missing: {names}")
    for pkg in missing:
        if pkg not in _WARNED:
            _WARNED.add(pkg)
            log.warning(
                "package %s (%s) not present: using its fake from contracts.testing.fakes",
                pkg,
                EXPECTED_PACKAGES[pkg].wp,
            )
    return {pkg: "real" if ok else "fake" for pkg, ok in present.items()}


def bundle_path() -> Path:
    return var_dir() / "seed" / "bundle.json"


def load_bundle_file(path: Path | None = None) -> SeedBundle | None:
    path = path or bundle_path()
    if not path.exists():
        return None
    return SeedBundle.model_validate(json.loads(path.read_text(encoding="utf-8")))


def current_bundle() -> SeedBundle:
    """The seeded bundle, or the tiny fixture when nothing was generated yet."""
    from contracts.testing.builders import load_tiny_bundle

    return load_bundle_file() or load_tiny_bundle()


@dataclass
class Components:
    """Everything ``App`` holds. Built by ``build_components``; tests may swap parts."""

    settings: Settings
    clock: Clock
    ids: IdFactory
    engine: Engine | None
    modes: dict[str, str]
    business: dict[str, str]
    books: Any
    outputs: Any
    lookup: EntityLookupImpl
    executor: ExecutorImpl
    gate: Any
    audit: Any
    controls: Any
    ports: Any
    sink: EventSink
    agent: Any
    forecaster: Any
    cash_planner: Any
    financing: Any
    readiness: Any
    transport: Any = None
    extras: dict[str, Any] = field(default_factory=dict)


def _module(name: str) -> Any:
    return importlib.import_module(name)


def _books(mode: str, engine: Engine, clock: Clock, ids: IdFactory, settings: Settings) -> Any:
    if mode == "real":
        return _module("books.service").Books(engine, clock, ids, settings)
    from contracts.testing.fakes import FakeBooks

    return FakeBooks(current_bundle())


def _outputs(
    mode: str, engine: Engine, clock: Clock, ids: IdFactory, settings: Settings, name: str
) -> Any:
    if mode == "real":
        # D4 (PC B): Settings has no business name, so pass the seeded one.
        return _module("outputs.service").Outputs(engine, clock, ids, settings, business_name=name)
    from contracts.testing.fakes import FakeOutputs

    return FakeOutputs(var_dir() / "packs", name)


def _governance(
    mode: str,
    engine: Engine,
    clock: Clock,
    ids: IdFactory,
    lookup: EntityLookupImpl,
    executor: ExecutorImpl,
    settings: Settings,
) -> tuple[Any, Any, Any]:
    if mode == "real":
        return _module("governance.service").build_governance(
            engine, clock, ids, lookup, executor, settings
        )
    from contracts.testing.fakes import FakeAudit, FakeControls, FakeGate

    audit = FakeAudit(clock)
    return FakeGate(executor=executor, audit=audit, clock=clock), audit, FakeControls()


def _ports(mode: str, settings: Settings, verifier: Any, use_inprocess: bool) -> tuple[Any, Any]:
    """(ports, transport). The transport is closed by ``App.close()``."""
    if mode == "real":
        transport = None
        if use_inprocess:
            from infra.sandbox_runner import asgi_transport

            transport = asgi_transport()
        return _module("adapters").build_ports(settings, verifier, transport), transport
    from contracts.testing.fakes import make_fake_ports

    return make_fake_ports(current_bundle(), verifier), None


def _agent(mode: str, settings: Settings, sink: EventSink) -> Any:
    if mode == "real":
        llm = _module("agent.llm").LLM(settings, events=sink)
        return _module("agent.service").Agent(llm, events=sink)
    from contracts.testing.fakes import FakeAgent

    return FakeAgent()


def _planning(mode: str) -> tuple[Any, Any]:
    if mode == "real":
        planner = _module("planning.service").Planner()
        return planner, planner
    from contracts.testing.fakes import FakeCashPlan, FakeForecast

    return FakeForecast(), FakeCashPlan()


def _financing(mode: str) -> tuple[Any, Any]:
    if mode == "real":
        mod = _module("financing.service")
        return (
            mod.Financing(DATA_DIR / "products.json", DATA_DIR / "regulatory_params.json"),
            mod.Readiness(),
        )
    from contracts.testing.fakes import FakeFinancing, FakeReadiness

    return FakeFinancing(), FakeReadiness()


def build_components(
    settings: Settings | None = None,
    *,
    inprocess_sandbox: bool = True,
    fakes: FakesMode = "auto",
    engine: Engine | None = None,
) -> Components:
    """Wire every service in the PRD/11 §2 order."""
    versions.check()  # ORC-02: a contract major mismatch stops start-up here
    settings = settings or get_settings()
    modes = resolve_modes(fakes)
    ensure_dirs()
    clock, ids = get_clock(), get_id_factory()
    eng = engine or default_engine("app")
    create_all(eng)
    business = dict(current_bundle().business)
    name = business.get("name", "LoanReady business")

    books = _books(modes["books"], eng, clock, ids, settings)
    outputs = _outputs(modes["outputs"], eng, clock, ids, settings, name)
    lookup = EntityLookupImpl(books, outputs)
    executor = ExecutorImpl(books, outputs, name)
    gate, audit, controls = _governance(
        modes["governance"], eng, clock, ids, lookup, executor, settings
    )
    use_inprocess = inprocess_sandbox and settings.sandbox_mode == "inprocess"
    ports, transport = _ports(modes["adapters"], settings, gate.ticket_verifier(), use_inprocess)
    executor.ports = ports
    sink = EventSink(audit)
    agent = _agent(modes["agent"], settings, sink)
    forecaster, cash_planner = _planning(modes["planning"])
    financing, readiness = _financing(modes["financing"])
    return Components(
        settings=settings,
        clock=clock,
        ids=ids,
        engine=eng,
        modes=modes,
        business=business,
        books=books,
        outputs=outputs,
        lookup=lookup,
        executor=executor,
        gate=gate,
        audit=audit,
        controls=controls,
        ports=ports,
        sink=sink,
        agent=agent,
        forecaster=forecaster,
        cash_planner=cash_planner,
        financing=financing,
        readiness=readiness,
        transport=transport,
    )


def build_app(
    settings: Settings | None = None,
    *,
    inprocess_sandbox: bool = True,
    fakes: FakesMode = "auto",
    engine: Engine | None = None,
) -> Any:
    """The ``AppFacade`` for the UI, scripts and tests (``engine`` overrides var/app.db)."""
    from orchestrator.facade import App

    return App(
        build_components(settings, inprocess_sandbox=inprocess_sandbox, fakes=fakes, engine=engine)
    )
