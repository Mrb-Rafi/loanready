"""Compliance matrix (PRD/13 §1-2): ``docs/generated/compliance_matrix.md`` and
``docs/compliance_matrix.md`` from ``controls_table()`` (``governance/controls.py``), the
regulation names in PRD/14 §2 and the evidence CSV (rows per CTL id).

Wording follows PRD/14 §4: "aligned with" / "designed around", never "compliant".
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from infra.paths import REPO_ROOT
from orchestrator.container import build_app
from scripts._common import (
    DOCS_DIR,
    GENERATED_DIR,
    GENERATED_NOTE,
    mock_settings,
    regulation_names,
    table,
    write_md,
)

EVIDENCE_CSV = REPO_ROOT / "evidence" / "action_log.csv"

PREFACE = """# Compliance matrix

LoanReady is a prototype on synthetic data. This matrix shows how its controls are
**designed around** the regulations and standards listed in PRD/14: each control names the
code that implements it, the automated tests that check it and how many rows of the demo's
action log carry its id. It is an alignment map, **not** a certification or a legal opinion:
LoanReady is not "compliant" with, "certified" under or "approved" by any of these regimes.

"""


def evidence_counts(path: Path = EVIDENCE_CSV) -> Counter[str]:
    """Rows per CTL id in the exported action log (its ``control_ids`` column)."""
    counts: Counter[str] = Counter()
    csv.field_size_limit(2**31 - 1)  # CATEGORISE rows list thousands of raw ids
    if not path.exists():
        return counts
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            raw = row.get("control_ids") or "[]"
            for cid in json.loads(raw):
                counts[cid] += 1
    return counts


def render(controls: list[Any], names: dict[str, str], counts: Counter[str], csv_ok: bool) -> str:
    note = GENERATED_NOTE.format(
        source="`governance/controls.py`, PRD/14 §2 and `evidence/action_log.csv`"
    )
    evidence_note = (
        "Evidence rows are counted from `evidence/action_log.csv` (run `python tasks.py demo`)."
        if csv_ok
        else "No `evidence/action_log.csv` yet: run `python tasks.py demo` for evidence counts."
    )
    by_control = table(
        ["Control", "Title", "Regulations", "Implemented in", "Tests", "Evidence rows"],
        [
            (
                c.control_id,
                c.title,
                ", ".join(c.regulation_refs),
                ", ".join(f"`{p.strip()}`" for p in c.implemented_in.split(",")),
                ", ".join(f"`{t}`" for t in c.tests) or "none (not built)",
                counts.get(c.control_id, 0),
            )
            for c in controls
        ],
    )
    regs: dict[str, list[str]] = {}
    for c in controls:
        for reg in c.regulation_refs:
            regs.setdefault(reg, []).append(c.control_id)
    by_regulation = table(
        ["Regulation", "Name (PRD/14 §2)", "Controls", "Evidence rows"],
        [
            (reg, names.get(reg, ""), ", ".join(ids), sum(counts.get(i, 0) for i in ids))
            for reg, ids in sorted(regs.items())
        ],
    )
    return (
        f"{note}\n\n{evidence_note}\n\n## By regulation\n\n{by_regulation}\n"
        f"## By control\n\n{by_control}"
    )


def main(argv: list[str] | None = None) -> int:
    app = build_app(mock_settings())
    try:
        controls = app.controls_table()
    finally:
        app.close()
    counts = evidence_counts()
    body = render(controls, regulation_names(), counts, EVIDENCE_CSV.exists())
    gen = write_md(GENERATED_DIR / "compliance_matrix.md", "# Compliance matrix\n\n" + body)
    doc = write_md(DOCS_DIR / "compliance_matrix.md", PREFACE + body.split("\n", 2)[2])
    print(f"wrote {gen} and {doc} ({len(controls)} controls, {sum(counts.values())} tags)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
