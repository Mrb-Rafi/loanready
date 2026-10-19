"""``THIRD_PARTY.md`` from ``requirements.lock`` and installed package metadata
(PRD/13 §2; competition licensing rule). ``python -m scripts.licenses``.

Licences come from ``importlib.metadata`` (``License-Expression``, else a short ``License``
field, else the OSI classifiers). Packages whose platform marker excludes this OS are not
installed here; their licence is taken from ``PLATFORM_ONLY`` (checked by hand).
"""

from __future__ import annotations

import re
import sys
from importlib import metadata
from pathlib import Path

from infra.paths import REPO_ROOT
from scripts._common import table, write_md

LOCK = REPO_ROOT / "requirements.lock"
OUT = REPO_ROOT / "THIRD_PARTY.md"

# Lock entries limited to another OS by a marker (not installed on every PC).
PLATFORM_ONLY = {
    "colorama": "BSD-3-Clause",
    "tzdata": "Apache-2.0",
    "uvloop": "MIT OR Apache-2.0",
}

# Metadata too vague to print as is (checked by hand against the project's licence files).
OVERRIDES = {"python-dateutil": "Apache-2.0 OR BSD-3-Clause (dual)"}

DIRECT = {
    "fastapi", "uvicorn", "sqlmodel", "sqlalchemy", "pydantic", "pydantic-settings", "httpx",
    "streamlit", "pandas", "reportlab", "pillow", "pytesseract", "ollama", "openai", "pytest",
    "pytest-cov", "hypothesis", "ruff", "import-linter", "altair",
}  # fmt: skip

PROSE = (Path(__file__).parent / "templates" / "third_party.md").read_text(encoding="utf-8")


def lock_entries() -> list[tuple[str, str, str]]:
    """``(name, version, marker)`` for every pinned line."""
    out = []
    for line in LOCK.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        spec, _, marker = line.partition(";")
        name, _, version = spec.strip().partition("==")
        out.append((name.strip(), version.strip(), marker.strip()))
    return out


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_of(name: str) -> str:
    if _norm(name) in OVERRIDES:
        return OVERRIDES[_norm(name)]
    try:
        meta = metadata.metadata(name)
    except metadata.PackageNotFoundError:
        return PLATFORM_ONLY.get(_norm(name), "not installed on this platform")
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) <= 60 and "\n" not in lic and lic.upper() != "UNKNOWN":
        return lic
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::") and "OSI Approved" in c and c.count("::") >= 2
    ]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    if classifiers:
        return " / ".join(dict.fromkeys(classifiers))
    return lic.splitlines()[0][:60] if lic else "see package metadata"


def render() -> str:
    rows = []
    for name, version, marker in sorted(lock_entries(), key=lambda e: _norm(e[0])):
        note = "Direct" if _norm(name) in DIRECT else ""
        if marker:
            note = (note + "; " if note else "") + f"only where `{marker}`"
        rows.append((name, version, licence_of(name), note))
    return PROSE.format(table=table(["Package", "Version", "Licence", "Notes"], rows))


def main(argv: list[str] | None = None) -> int:
    path = write_md(OUT, render())
    print(f"wrote {path} ({len(lock_entries())} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
