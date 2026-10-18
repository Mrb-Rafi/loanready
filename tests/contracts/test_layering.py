"""T-CON-03 / CTL-CHG-02: layering rules checked with ``ast`` (runs even without
import-linter). Mirrors ARC-04..06 and ``.importlinter``."""

from __future__ import annotations

import ast
from pathlib import Path

from infra.paths import REPO_ROOT

LAYER2 = {"governance", "books", "planning", "financing", "outputs", "agent", "adapters", "datagen"}
FIRST_PARTY = {"contracts", "infra", "sandbox", "orchestrator", "ui", "scripts"} | LAYER2
ALLOWED: dict[str, set[str]] = {
    "contracts": {"contracts"},
    "infra": {"contracts", "infra"},
    **{p: {"contracts", "infra", p} for p in LAYER2},
    "orchestrator": {"contracts", "infra", "orchestrator"} | LAYER2,
    "scripts": {"contracts", "infra", "orchestrator", "scripts"} | LAYER2,
    "ui": {"contracts", "orchestrator", "ui"},
    "sandbox": {"contracts", "sandbox"},
}
SANDBOX_CONTRACTS = {
    "contracts",
    "contracts.sandbox_api",
    "contracts.seed",
    "contracts.money",
    "contracts.canonical",
    "contracts.enums",
    "contracts.clock",
}


def imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append(node.module)
    return out


def violations(root: Path = REPO_ROOT) -> list[str]:
    bad = []
    for pkg, allowed in ALLOWED.items():
        base = root / pkg
        if not base.exists():
            continue
        for f in base.rglob("*.py"):
            for mod in imports_of(f):
                top = mod.split(".")[0]
                if top in FIRST_PARTY and top not in allowed:
                    bad.append(f"{f.relative_to(root).as_posix()} imports {mod}")
                if pkg == "sandbox" and top == "contracts" and mod not in SANDBOX_CONTRACTS:
                    bad.append(
                        f"{f.relative_to(root).as_posix()} imports {mod} (sandbox may use only "
                        "sandbox_api, seed, money, canonical, enums, clock)"
                    )
    return bad


def test_layering_holds() -> None:
    """T-CON-03 proves ARC-04, ARC-05, ARC-06, CTL-CHG-02 on the current tree."""
    assert violations() == []


def test_detects_cross_layer_import(tmp_path: Path) -> None:
    """T-CON-03 proves ARC-04: a Layer 2 package importing another is reported."""
    (tmp_path / "books").mkdir()
    (tmp_path / "books" / "x.py").write_text(
        "from governance import gate\nimport contracts.money\n", encoding="utf-8"
    )
    (tmp_path / "ui").mkdir()
    (tmp_path / "ui" / "y.py").write_text("def f():\n    import infra.db\n", encoding="utf-8")
    assert violations(tmp_path) == ["books/x.py imports governance", "ui/y.py imports infra.db"]
