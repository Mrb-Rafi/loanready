"""Contract version check (ARC-02): every present package's ``REQUIRES_CONTRACTS`` must
match the major version of ``contracts.CONTRACTS_VERSION``."""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass

from contracts import CONTRACTS_MAJOR, CONTRACTS_VERSION
from contracts.registry import EXPECTED_PACKAGES


@dataclass(frozen=True)
class PackageVersion:
    package: str
    wp: str
    present: bool
    requires: str | None
    ok: bool


def package_versions() -> list[PackageVersion]:
    out = []
    for pkg, spec in EXPECTED_PACKAGES.items():
        if importlib.util.find_spec(pkg) is None:
            out.append(PackageVersion(pkg, spec.wp, False, None, True))
            continue
        module = importlib.import_module(pkg)
        requires = getattr(module, "REQUIRES_CONTRACTS", None)
        ok = requires is not None and str(requires).split(".")[0] == CONTRACTS_MAJOR
        out.append(
            PackageVersion(pkg, spec.wp, True, None if requires is None else str(requires), ok)
        )
    return out


def check() -> list[PackageVersion]:
    """Fail fast with a message naming every package built against another major."""
    rows = package_versions()
    bad = [r for r in rows if r.present and not r.ok]
    if bad:
        names = ", ".join(f"{r.package} ({r.wp}) requires {r.requires!r}" for r in bad)
        raise RuntimeError(
            f"contracts {CONTRACTS_VERSION} (major {CONTRACTS_MAJOR}) does not match: {names}. "
            "Rebuild those packages against the current contracts."
        )
    return rows
