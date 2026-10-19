"""LoanReady task runner (WP0). Same commands on Windows and Linux.

    python tasks.py <target> [args]

Targets: setup, lint, test [pkg] [--all] [--optional], sandbox, gen, seed, ui, demo,
evidence, docs, integrate, clean, doctor [--ports], freeze, handoff <wp...>,
assemble <zip...> [--dry-run], run.

Once ``.venv`` exists this script re-launches itself with the venv interpreter, so
``python tasks.py ...`` behaves the same on every PC after ``setup``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
BUSINESS_PACKAGES = ("governance", "books", "planning", "financing", "outputs", "agent", "adapters")
NEEDS = {  # target -> (module, package, WP)
    "sandbox": ("sandbox.app", "sandbox", "WP2"),
    "gen": ("datagen.generate", "datagen", "WP8"),
    "seed": ("orchestrator.seed", "orchestrator", "WP9"),
    "ui": ("ui.app", "ui", "WP10"),
    "demo": ("scripts.run_demo", "scripts", "WP11"),
    "evidence": ("scripts.export_evidence", "scripts", "WP11"),
}
INTEGRATE_STYLE = {"integrate", "docs"}


def _in_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV.resolve()
    except OSError:
        return False


def _relaunch_in_venv() -> None:
    if VENV_PY.exists() and not _in_venv() and os.environ.get("LOANREADY_NO_RELAUNCH") != "1":
        sys.exit(
            subprocess.call([str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
        )


def _py(*args: str, env: dict[str, str] | None = None) -> int:
    return subprocess.call([sys.executable, *args], cwd=ROOT, env={**os.environ, **(env or {})})


def _module_available(module: str) -> bool:
    sys.path.insert(0, str(ROOT))
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False
    finally:
        sys.path.remove(str(ROOT))


def _require(target: str) -> bool:
    module, pkg, wp = NEEDS[target]
    if _module_available(module):
        return True
    print(f"target {target} needs package {pkg} ({wp}), not present yet")
    return False


def setup(_: list[str]) -> int:
    if sys.version_info[:2] != (3, 12):
        print(
            f"warning: Python {sys.version_info[0]}.{sys.version_info[1]}; the team pins 3.12 "
            "(Windows: `py -3.12 tasks.py setup`; Linux: `python3.12 tasks.py setup`)"
        )
    if not VENV_PY.exists():
        print("creating .venv ...")
        rc = subprocess.call([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT)
        if rc:
            return rc
    print("installing requirements.lock ...")
    env = {**os.environ, "PIP_NO_CACHE_DIR": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    rc = subprocess.call(
        [str(VENV_PY), "-m", "pip", "install", "-r", str(ROOT / "requirements.lock")],
        cwd=ROOT,
        env=env,
    )
    if rc:
        return rc
    rc = subprocess.call(
        [str(VENV_PY), "-c", "from infra.paths import ensure_dirs; print('var:', ensure_dirs())"],
        cwd=ROOT,
    )
    if not (ROOT / ".env").exists():
        print(
            "hint: copy .env.example to .env "
            "(Windows: copy .env.example .env; Linux: cp .env.example .env)"
        )
    return rc


def lint(_: list[str]) -> int:
    rc = _py("-m", "ruff", "check", ".")
    rc |= _py("-m", "ruff", "format", "--check", ".")
    exe = Path(sys.executable).parent / ("lint-imports.exe" if os.name == "nt" else "lint-imports")
    rc |= subprocess.call(
        [str(exe)] if exe.exists() else [sys.executable, "-m", "importlinter.cli"], cwd=ROOT
    )
    return rc


def test(args: list[str]) -> int:
    all_ = "--all" in args
    optional = "--optional" in args
    pkgs = [a for a in args if not a.startswith("--")]
    markers = []
    if not all_:
        markers.append("not slow")
    if not optional:
        markers.append("not optional")
    cmd = ["-m", "pytest", "-q"]
    if markers:
        cmd += ["-m", " and ".join(markers)]
    paths = []
    for p in pkgs:
        path = ROOT / "tests" / p
        if not path.exists():
            print(f"no tests for {p!r} yet (tests/{p}/ missing)")
            return 1
        paths.append(f"tests/{p}")
        if p in BUSINESS_PACKAGES:
            cmd += [f"--cov={p}", "--cov-report=term"]
    if not pkgs:
        cmd += [f"--cov={p}" for p in BUSINESS_PACKAGES if (ROOT / p / "service.py").exists()]
        if any((ROOT / p / "service.py").exists() for p in BUSINESS_PACKAGES):
            cmd += ["--cov-report=term"]
    rc = _py(*cmd, *paths)
    return 0 if rc == 5 else rc  # 5 = no tests collected


def sandbox(_: list[str]) -> int:
    if not _require("sandbox"):
        return 1
    return _py("-m", "uvicorn", "sandbox.app:app", "--host", "127.0.0.1", "--port", "8100")


def gen(args: list[str]) -> int:
    return _py("-m", "datagen.generate", *args) if _require("gen") else 1


def seed(args: list[str]) -> int:
    return _py("-m", "orchestrator.seed", *args) if _require("seed") else 1


def ui(_: list[str]) -> int:
    if not _require("ui"):
        return 1
    env = {"LOANREADY_ENV": "dev"}
    return _py(
        "-m",
        "streamlit",
        "run",
        "ui/app.py",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
        env=env,
    )


def demo(args: list[str]) -> int:
    return (
        _py("-m", "scripts.run_demo", *args, env={"LOANREADY_ENV": "demo"})
        if _require("demo")
        else 1
    )


def evidence(args: list[str]) -> int:
    return _py("-m", "scripts.export_evidence", *args) if _require("evidence") else 1


def docs(_: list[str]) -> int:
    rc = 0
    for module in (
        "scripts.dump_tiers",
        "scripts.dump_controls",
        "scripts.licenses",
        "scripts.trace",
    ):
        if _module_available(module):
            rc |= _py("-m", module)
        else:
            print(f"target docs needs {module} (WP11), not present yet")
    return rc


def integrate(args: list[str]) -> int:
    return _py("-m", "infra.integration_check", *args)


def clean(_: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    from infra.paths import var_dir
    from infra.sandbox_runner import port_in_use

    for port in (8100, 8501):
        if port_in_use("127.0.0.1", port):
            print(
                f"refusing to clean: something is listening on port {port} "
                "(stop the UI/sandbox first)"
            )
            return 1
    var = var_dir()
    if not var.exists():
        print(f"nothing to clean ({var})")
        return 0
    for child in var.iterdir():
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except PermissionError as exc:
            print(
                f"refusing to continue: {child} is in use ({exc}); stop the UI/sandbox, then clean"
            )
            return 1
    print(f"cleaned {var}")
    return 0


def doctor(args: list[str]) -> int:
    return _py("-m", "infra.doctor", *args)


def freeze(args: list[str]) -> int:
    return _py("-m", "infra.freeze", *args)


def handoff(args: list[str]) -> int:
    return _py("-m", "infra.handoff", *args)


def assemble(args: list[str]) -> int:
    return _py("-m", "infra.assemble", *args)


def run(_: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    from infra.db import db_path

    if not db_path("app").exists():
        rc = seed([])
        if rc:
            return rc
    return ui([])


TARGETS = {
    "setup": setup,
    "lint": lint,
    "test": test,
    "sandbox": sandbox,
    "gen": gen,
    "seed": seed,
    "ui": ui,
    "demo": demo,
    "evidence": evidence,
    "docs": docs,
    "integrate": integrate,
    "clean": clean,
    "doctor": doctor,
    "freeze": freeze,
    "handoff": handoff,
    "assemble": assemble,
    "run": run,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    target, rest = argv[0], argv[1:]
    if target not in TARGETS:
        print(f"unknown target {target!r}; choose from: {', '.join(TARGETS)}")
        return 2
    if target != "setup":
        _relaunch_in_venv()
    rc = TARGETS[target](rest)
    if rc and target in NEEDS and not _module_available(NEEDS[target][0]):
        return 0 if target in INTEGRATE_STYLE else 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
