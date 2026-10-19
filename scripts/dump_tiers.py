"""``docs/generated/tier_table.md`` from the gate's tier table (PRD/13 §1, CTL-AC-01/02)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from contracts.enums import Tier
from orchestrator.container import build_app
from scripts._common import GENERATED_DIR, GENERATED_NOTE, mock_settings, table, write_md

TIER_HELP = {
    "T0_OBSERVE": "Tier 0: observe and compute only; runs at once",
    "T1_PROPOSE": "Tier 1: drafts and proposals only; nothing leaves LoanReady",
    "T2_APPROVE": "Tier 2: executes only after a human approval bound to the exact parameters",
    "T3_AUTONOMOUS": "Tier 3: autonomous execution; disabled in this prototype",
    "BLOCKED": "Blocked: never executes, whoever asks",
}
ORDER = {t.name: t.value for t in Tier}


def render(rows: list[Any]) -> str:
    tiers = sorted({r.tier for r in rows}, key=lambda t: ORDER.get(t, 999))
    legend = "\n".join(f"- `{t}`: {TIER_HELP.get(t, t)}" for t in tiers)
    body = table(
        ["Action type", "Tier", "What it does", "Safeguards"],
        [
            (f"`{r.action_type}`", r.tier, r.description, "; ".join(r.safeguards))
            for r in sorted(rows, key=lambda r: (ORDER.get(r.tier, 999), r.action_type))
        ],
    )
    return (
        "# Permission tiers\n\n"
        + GENERATED_NOTE.format(source="`AppFacade.tier_table()`, i.e. `governance.tiers`")
        + "\n\n"
        + legend
        + "\n\n"
        + body
    )


def main(argv: list[str] | None = None) -> int:
    app = build_app(mock_settings())
    try:
        rows = app.tier_table()
    finally:
        app.close()
    path: Path = write_md(GENERATED_DIR / "tier_table.md", render(rows))
    print(f"wrote {path} ({len(rows)} action types)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
