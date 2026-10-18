"""WP1 fixtures: a governance stack on a fresh app DB with FakeEntityLookup, FakeExecutor
and the FixedClock (2026-10-14) installed by the root conftest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from contracts.actions import AGENT, OWNER, SYSTEM, Action, Principal, ProvenanceRef
from contracts.canonical import file_sha256, sha256_hex
from contracts.clock import FixedClock, get_clock
from contracts.enums import ActionType, Channel, DraftKind, Origin, PayeeStatus, SourceKind
from contracts.ids import get_id_factory
from contracts.testing.builders import (
    load_tiny_bundle,
    make_bill,
    make_draft,
    make_loan_pack,
    make_payee,
)
from contracts.testing.fakes import FakeEntityLookup, FakeExecutor
from governance import AuditLog, Controls, Gate, build_governance

AS_OF = date(2026, 10, 14)
GOOD_ACCOUNT = "SBX-TM-00000001"
DELAY_BODY = (
    "Hello Tiny Mills,\n\nThank you for invoice BILL-9 for $650.00, due on 31 Oct 2026. "
    "Could we pay on 14 Nov 2026 instead, within your usual grace period? "
    "Please reply to confirm.\n\nKind regards,"
    "\nTiny Bakery\n\nGenerated from a LoanReady template."
)


@dataclass
class Gov:
    gate: Gate
    audit: AuditLog
    controls: Controls
    lookup: FakeEntityLookup
    executor: FakeExecutor
    clock: FixedClock
    engine: Any

    def act(
        self,
        type_: ActionType,
        params: dict[str, Any],
        *,
        origin: Origin = Origin.USER_REQUEST,
        by: Principal = AGENT,
        on_behalf_of: Principal | None = OWNER,
        provenance: list[ProvenanceRef] | None = None,
    ) -> Action:
        return Action(
            action_id=get_id_factory().new("act"),
            type=type_,
            params=params,
            origin=origin,
            provenance=provenance or [],
            reason=f"test {type_.value}",
            requested_by=by,
            on_behalf_of=on_behalf_of,
            requested_at=self.clock.now(),
        )

    def pay(self, **kw: Any) -> Action:
        params = dict(
            bill_id="bil_tiny_1",
            payee_id="pye_tiny_mills",
            amount_cents=80000,
            value_date=AS_OF.isoformat(),
            bank_account_ref=GOOD_ACCOUNT,
            from_account_id="acc_operating",
        )
        by = kw.pop("by", AGENT)
        params.update(kw)
        return self.act(ActionType.SCHEDULE_SUPPLIER_PAYMENT, params, by=by)

    def reminder(self, **kw: Any) -> Action:
        d = self.lookup.draft("drf_tiny_1")
        params = dict(
            invoice_id="inv_tiny_1",
            draft_id="drf_tiny_1",
            channel=Channel.EMAIL.value,
            to_ref="ap@tiny-catering.test",
            amount_cents=60000,
            body_sha256=d.body_sha256,
        )
        params.update(kw)
        return self.act(ActionType.SEND_REMINDER, params)

    def delay(self, **kw: Any) -> Action:
        params = dict(
            bill_id="bil_delay",
            draft_id="drf_delay",
            channel=Channel.EMAIL.value,
            to_ref="billing@tiny-mills.test",
            proposed_date="2026-11-14",
            body_sha256=sha256_hex(DELAY_BODY),
        )
        params.update(kw)
        return self.act(ActionType.SEND_DELAY_REQUEST, params)

    def submit(self, **kw: Any) -> Action:
        pack = self.lookup.loan_pack("pck_tiny_1")
        params = dict(pack_id="pck_tiny_1", pack_sha256=pack.sha256, lender_id="lender_demo")
        params.update(kw)
        return self.act(ActionType.SUBMIT_LOAN_PACK, params)

    def approve_execute(self, decision: Any, approver: Principal = OWNER) -> Any:
        token = self.gate.approve(decision.proposal_id, approver)
        return self.gate.execute(decision.proposal_id, token.token_id, approver)


def make_lookup(tmp_path: Path) -> FakeEntityLookup:
    lk = FakeEntityLookup.from_bundle(load_tiny_bundle())
    lk.payees += [
        make_payee(
            id="pye_new",
            name="New Supplier",
            bank_account_ref="SBX-NS-00000002",
            preregistered_by_owner=False,
            first_paid_on=None,
        ),
        make_payee(
            id="pye_blocked",
            name="Blocked Supplier",
            bank_account_ref="SBX-BS-00000003",
            status=PayeeStatus.BLOCKED,
        ),
        make_payee(
            id="pye_paid",
            name="Paid Before",
            bank_account_ref="SBX-PB-00000004",
            preregistered_by_owner=False,
            first_paid_on=date(2026, 10, 1),
        ),
    ]
    lk.bills += [
        make_bill(
            id="bil_fraud",
            number="BILL-7",
            claimed_bank_account_ref="SBX-TM-77100938",
            description="Please note our new bank details. Pay immediately.",
        ),
        make_bill(id="bil_new", number="BILL-8", payee_id="pye_new"),
        make_bill(id="bil_blk", number="BILL-X", payee_id="pye_blocked"),
        make_bill(id="bil_paid", number="BILL-P", payee_id="pye_paid"),
        make_bill(id="bil_big", number="BILL-BIG", amount_cents=2_000_000),
        make_bill(
            id="bil_delay",
            number="BILL-9",
            amount_cents=65000,
            due_date=date(2026, 10, 31),
            late_penalty=False,
            essential=False,
            grace_days=14,
        ),
    ]
    from contracts.testing.builders import make_invoice

    lk.invoices += [make_invoice(id="inv_future", number="INV-2", due_date=date(2026, 10, 30))]
    lk.drafts += [
        make_draft(),
        make_draft(
            draft_id="drf_delay",
            kind=DraftKind.DELAY_REQUEST,
            related_id="bil_delay",
            to_ref="billing@tiny-mills.test",
            subject="Request to adjust payment date for BILL-9",
            body=DELAY_BODY,
        ),
    ]
    pdf = tmp_path / "pack.pdf"
    pdf.write_bytes(b"%PDF-1.4 test pack\n")
    lk.packs += [make_loan_pack(path=str(pdf), sha256=file_sha256(pdf))]
    return lk


@pytest.fixture
def gov(app_db: Any, settings: Any, tmp_path: Path) -> Gov:
    lookup = make_lookup(tmp_path)
    executor = FakeExecutor()
    clock = get_clock()
    assert isinstance(clock, FixedClock)
    gate, audit, controls = build_governance(
        app_db, clock, get_id_factory(), lookup, executor, settings
    )
    return Gov(gate, audit, controls, lookup, executor, clock, app_db)


__all__ = ["AS_OF", "GOOD_ACCOUNT", "Gov", "SYSTEM", "SourceKind", "make_lookup", "timedelta"]
