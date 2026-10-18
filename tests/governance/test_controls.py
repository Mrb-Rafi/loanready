"""T-GOV-24: the controls registry is complete and points at real code."""

from __future__ import annotations

import importlib
import importlib.util
import re

from contracts.registry import CONTROL_IDS, EXPECTED_PACKAGES
from governance.controls import CONTROLS, COSO, Controls
from infra.paths import REPO_ROOT

ALWAYS_PRESENT = {"contracts", "infra", "governance"}


def _built(top: str) -> bool:
    spec = EXPECTED_PACKAGES.get(top)
    if top in ALWAYS_PRESENT:
        return True
    if spec is None:
        return False
    try:
        mod = importlib.import_module(spec.entry_module)
    except Exception:
        return False
    return all(hasattr(mod, n) for n in spec.service_names)


def _resolve(path: str) -> bool:
    parts = path.split(".")
    for i in range(len(parts), 0, -1):
        module = ".".join(parts[:i])
        try:
            if importlib.util.find_spec(module) is None:
                continue
        except ModuleNotFoundError:
            continue
        obj = importlib.import_module(module)
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def test_registry_complete_and_unique() -> None:
    """T-GOV-24 proves GOV-19, PRD-31: every CTL id of PRD/14 §3 is present exactly once,
    with a COSO component, regulations and tests."""
    ids = [c.control_id for c in Controls().controls()]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(CONTROL_IDS)
    assert set(COSO.values()) <= {"CE", "RA", "CA", "IC", "MO"}
    for c in CONTROLS:
        assert c.regulation_refs and c.tests and c.implemented_in, c.control_id


def test_implemented_in_importable_when_present() -> None:
    """T-GOV-24 proves GOV-19: every implemented_in path imports when its package is built
    (absent packages are skipped until assembled)."""
    missing = []
    for c in CONTROLS:
        for path in (p.strip() for p in c.implemented_in.split(",")):
            if _built(path.split(".")[0]) and not _resolve(path):
                missing.append(f"{c.control_id}: {path}")
    assert missing == []


def test_ctl_ids_used_in_governance_exist() -> None:
    """T-GOV-24 proves GOV-19: every CTL id referenced in governance/ is in the registry."""
    used = set()
    for f in (REPO_ROOT / "governance").rglob("*.py"):
        used |= set(re.findall(r"CTL-[A-Z]+-\d\d", f.read_text(encoding="utf-8")))
    assert used and used <= set(CONTROL_IDS)
