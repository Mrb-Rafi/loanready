"""Headless Rina story (PRD/13 §1, PRD/00 §4 steps 1-10): ``python tasks.py demo``.

Runs every step through the ``AppFacade`` with ``LOANREADY_ENV=demo`` (FixedClock at AS_OF,
seeded ids). The script plays the human owner: it resolves the confirm queue and approves
each Tier 2 proposal, so the audit log records ``approved_by`` = the owner principal.

Writes ``evidence/demo_summary.json`` and ``evidence/action_log.csv`` (step 10) and prints a
step report. Exits 1 if any S1-S10 check fails or the hash chain does not verify.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contracts.enums import ActionType, ConfirmKind, Decision, Lever, ReasonCode, Role, Scenario
from contracts.money import fmt, pct
from infra.paths import DATA_DIR, REPO_ROOT, ensure_dirs
from infra.settings import reset_settings
from orchestrator.container import build_app
from orchestrator.seed import load_or_generate_bundle, reset_app_db, seed_app

EVIDENCE_DIR = REPO_ROOT / "evidence"
DECISIONS = tuple(d.value for d in Decision)


@dataclass
class Report:
    """S1-S10 flags plus one console line per step."""

    scenarios: dict[str, bool] = field(default_factory=dict)
    lines: list[str] = field(default_factory=list)

    def step(self, n: int, title: str, detail: str) -> None:
        self.lines.append(f"{n:>2}. {title:<18} {detail}")
        print(self.lines[-1])

    def check(self, sid: str, ok: bool, detail: str) -> None:
        self.scenarios[sid] = self.scenarios.get(sid, True) and bool(ok)
        line = f"    {sid:<4} {'OK  ' if ok else 'FAIL'} {detail}"
        self.lines.append(line)
        print(line)


def _expected() -> dict[str, Any]:
    return json.loads((DATA_DIR / "expected.json").read_text(encoding="utf-8"))


def _source_refs(app: Any, raw_ids: set[str]) -> set[str]:
    """Confirm items point at books' raw rows; expected.json names the bank transactions."""
    from sqlmodel import Session

    from contracts.schema import RawTransaction

    with Session(app.c.engine) as s:
        rows = {rid: s.get(RawTransaction, rid) for rid in raw_ids}
    return {r.source_ref if r is not None else rid for rid, r in rows.items()}


def _approve(app: Any, decision: Any) -> Any:
    """The owner approves a pending Tier 2 proposal and it executes."""
    if decision.decision != Decision.PENDING_APPROVAL:
        return decision
    return app.approve_and_execute(decision.proposal_id)


def run(app: Any, exp: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    r = Report()
    app.set_principal(Role.OWNER)

    # 1. Capture (S1, S5, S8): POS, bank and receipts were ingested by seeding.
    queue = app.confirm_queue()
    personal = [i for i in queue if i.kind == ConfirmKind.PERSONAL]
    dups = [i for i in queue if i.kind == ConfirmKind.DUPLICATE]
    r.step(1, "Capture", f"{len(queue)} items to confirm")
    r.check(
        "S1",
        len(personal) == 3
        and _source_refs(app, {i.ref_id for i in personal}) == set(exp["S1"]["personal_ids"]),
        f"{len(personal)} personal expenses found",
    )
    r.check(
        "S5",
        bool(dups)
        and _source_refs(app, {i.ref_id for i in dups}) <= set(exp["S5"]["duplicate_ids"])
        and any(row.action_type == "DUPLICATE_FLAGGED" for row in app.audit_rows()),
        f"{len(dups)} duplicate charge flagged",
    )
    receipts = {x["file"]: x["status"] for x in app.c.extras["seed"]["receipts"]}
    r.check(
        "S8",
        receipts.get(exp["S8"]["receipt"]) == exp["S8"]["status"],
        f"{exp['S8']['receipt']}: {receipts.get(exp['S8']['receipt'])}",
    )
    # The owner confirms every bank line (personal -> drawings, duplicate -> refund claim,
    # the memo deposit); the blurry receipt stays in the queue as in the story.
    for item in queue:
        if item.kind != ConfirmKind.RECEIPT:
            app.resolve(item.id, item.proposed)

    # 2. Books: ledger cash equals bank + till after the owner's confirmations.
    app.books_view()
    cash = app.books.cash_balance(app.as_of)
    bank = app.ports.bank.get_balance("acc_operating")
    target = exp["closing_bank_cents"] + exp["closing_till_cents"]
    r.step(2, "Books", f"cash {fmt(cash)} = bank {fmt(bank)} + till; target {fmt(target)}")

    # 3. Forecast (S2)
    f = app.forecast()
    base = f.scenarios[Scenario.BASE]
    r.step(3, "Forecast", f"BASE first negative week {base.first_negative_week}")
    r.check(
        "S2",
        base.first_negative_week == exp["S2"]["first_negative_week_base"],
        f"week {base.first_negative_week}",
    )

    # 4. Collect (S3): reminders drafted, approved, sent
    reminders = app.draft_reminders()
    sent = [_approve(app, app.propose_send(d.draft_id)) for d in reminders]
    r.step(4, "Collect", f"{len(reminders)} reminders drafted and sent")
    r.check(
        "S3",
        sorted(d.related_id for d in reminders) == sorted(exp["S3"]["overdue_invoice_ids"])
        and all(s.decision == Decision.EXECUTED for s in sent),
        ", ".join(sorted(d.related_id for d in reminders)),
    )

    # 5-6. Delay and borrow (S4)
    plan = app.cash_plan()
    levers = [s.lever for s in plan.steps]
    delays = app.draft_delay_requests()
    delay_sent = [_approve(app, app.propose_send(d.draft_id)) for d in delays]
    r.step(5, "Delay", f"{len(delays)} delay request(s) drafted and sent")
    comp = app.financing()
    by_id = {o.product_id: o for o in comp.options}
    cheapest = by_id[comp.cheapest_id] if comp.cheapest_id else None
    dearest = by_id[comp.most_expensive_id] if comp.most_expensive_id else None
    request = plan.financing_request
    r.step(
        6,
        "Borrow (compare)",
        f"request {fmt(request.amount_cents) if request else 'none'}; "
        f"cheapest {comp.cheapest_id}, most expensive {comp.most_expensive_id}",
    )
    r.check(
        "S4",
        levers[:3] == [Lever.COLLECT, Lever.DELAY, Lever.BORROW]
        and request is not None
        and all(s.decision == Decision.EXECUTED for s in delay_sent)
        and cheapest is not None
        and cheapest.kind.value == exp["S4"]["cheapest_kind"]
        and comp.most_expensive_id == exp["S4"]["most_expensive_id"],
        " -> ".join(lv.value for lv in levers),
    )

    # 7. Pay a supplier (S10)
    pay = app.propose_supplier_payment(exp["S10"]["bill_id"])
    amount = app.gate.proposal(pay.proposal_id).action.params.get("amount_cents")
    paid = _approve(app, pay)
    r.step(7, "Pay a supplier", f"{exp['S10']['bill_id']}: {paid.decision.value}")
    r.check(
        "S10",
        amount == exp["S10"]["amount_cents"]
        and paid.decision == Decision.EXECUTED
        and bool(paid.result and paid.result.sbx_ref),
        f"{fmt(amount or 0)} ref {paid.result.sbx_ref if paid.result else None}",
    )

    # 8. Security moment (S6, S7, S9)
    balance_before = app.ports.bank.get_balance("acc_operating")
    fraud = app.propose_supplier_payment(exp["S6"]["bill_id"])
    r.step(8, "Security", f"fraud bill: {fraud.decision.value} {fraud.reason_code}")
    r.check(
        "S6",
        fraud.decision == Decision.BLOCKED
        and fraud.reason_code == ReasonCode(exp["S6"]["expected_reason"])
        and exp["S6"]["bill_id"] in {b.id for b in app.excluded_bills()},
        str(fraud.reason_code),
    )
    flags = [row for row in app.audit_rows() if row.action_type == "UNTRUSTED_INSTRUCTION_IGNORED"]
    r.check(
        "S7",
        len(flags) >= exp["S7"]["min_flags"]
        and app.pending() == []
        and app.ports.bank.get_balance("acc_operating") == balance_before,
        f"{len(flags)} planted instructions ignored and logged",
    )
    s9 = exp.get("S9")
    if s9:
        row = next((x for x in app.receivables().rows if x.invoice_id == s9["invoice_id"]), None)
        r.check(
            "S9",
            row is not None
            and row.bucket == s9["bucket"]
            and s9["invoice_id"] not in {d.related_id for d in reminders},
            f"{s9['invoice_id']} in {row.bucket if row else None}, no reminder",
        )

    # 9. Loan pack: readiness, build, submit after approval; accepting a loan stays blocked
    ready = app.readiness()
    amount_cents = request.amount_cents if request else 100000
    term_days = request.term_days if request else 30
    pack = app.build_loan_pack(amount_cents, term_days, "Working capital for the festival order")
    submitted = _approve(app, app.propose_submit_pack(pack.pack_id))
    receipt = submitted.result.sbx_ref if submitted.result else None
    accept = app.try_blocked(ActionType.ACCEPT_LOAN, {"application_id": receipt or ""})
    r.step(
        9,
        "Loan pack",
        f"readiness {ready.score}; pack {pack.sha256[:12]}; lender receipt {receipt}; "
        f"accept loan: {accept.decision.value}",
    )
    pack_ok = submitted.decision == Decision.EXECUTED and accept.decision == Decision.BLOCKED

    # 10. Evidence: export the action log and verify the chain
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = app.export_csv(out_dir / "action_log.csv")
    broken = app.verify_chain()
    rows = app.audit_rows()
    r.step(10, "Evidence", f"{len(rows)} log rows; chain {'OK' if broken is None else broken}")

    counts = {k: app.audit_counts().get(k, 0) for k in DECISIONS}
    summary = {
        "as_of": app.as_of.isoformat(),
        "seed": exp["seed"],
        "chain_ok": broken is None,
        "final_row_hash": rows[-1].row_hash if rows else None,
        "counts": counts,
        "scenarios": dict(sorted(r.scenarios.items(), key=lambda kv: int(kv[0][1:]))),
        "loan_pack_submitted": pack_ok,
        "books_reconciled": cash == target and bank == exp["closing_bank_cents"],
        "headline": {
            "cash_today": fmt(cash),
            "first_negative_week_base": base.first_negative_week,
            "recommended_gap": fmt(f.recommended_gap_cents),
            "borrow_request": fmt(amount_cents),
            "cheapest": f"{cheapest.name} (EAR {pct(cheapest.ear)})" if cheapest else None,
            "most_expensive": f"{dearest.name} (EAR {pct(dearest.ear)})" if dearest else None,
            "readiness": ready.score,
            "pack_sha256": pack.sha256,
            "lender_receipt": receipt,
        },
        "action_log_csv": csv_path.name,
        "steps": r.lines,
    }
    return summary


def ok(summary: dict[str, Any]) -> bool:
    return (
        summary["chain_ok"]
        and summary["loan_pack_submitted"]
        and summary["books_reconciled"]
        and all(summary["scenarios"].values())
        and len(summary["scenarios"]) >= 9
    )


def write_summary(summary: dict[str, Any], out_dir: Path) -> Path:
    path = out_dir / "demo_summary.json"
    text = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.run_demo", description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=EVIDENCE_DIR)
    args = ap.parse_args(argv)
    reset_settings()  # fresh FixedClock and seeded ids: two runs give identical evidence
    ensure_dirs()
    bundle, source = load_or_generate_bundle(generate=True)
    reset_app_db()
    app = build_app(fakes=False)
    try:
        app.c.extras["seed"] = seed_app(app, bundle)
        print(f"Rina story, AS_OF {app.as_of} (bundle from {source})")
        summary = run(app, _expected(), args.out_dir)
    finally:
        app.close()
    path = write_summary(summary, args.out_dir)
    good = ok(summary)
    print(f"{'ALL CHECKS PASSED' if good else 'SOME CHECKS FAILED'}; summary: {path}")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
