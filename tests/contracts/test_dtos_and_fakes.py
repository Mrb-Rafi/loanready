"""T-CON-02: every DTO round-trips through JSON; every fake satisfies its Protocol."""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import contracts.actions
import contracts.dto_agent
import contracts.dto_app
import contracts.dto_books
import contracts.dto_financing
import contracts.dto_outputs
import contracts.dto_planning
import contracts.ports as ports_mod
import contracts.sandbox_api
import contracts.seed
import contracts.services as services_mod
from contracts._model import Frozen
from contracts.actions import (
    AGENT,
    OWNER,
    ApprovalSummary,
    ApprovalToken,
    AuditRecord,
    ExecutionResult,
    ProvenanceRef,
)
from contracts.dto_agent import LLMRequest, LLMResponse, RouterResult
from contracts.dto_app import ChatReply, DemoStepResult
from contracts.dto_books import CategorisationResult, JournalEntryDTO, JournalLineDTO
from contracts.dto_outputs import LoanPackContent
from contracts.dto_planning import DriverItem, EventUplift
from contracts.enums import (
    ActionType,
    CategorisationMethod,
    DataClass,
    EntrySource,
    EntryStatus,
    Framework,
    Intent,
    LLMBackend,
    LLMTask,
    SourceKind,
    Tier,
)
from contracts.registry import CHART_OF_ACCOUNTS
from contracts.sandbox_api import (
    AdminPayeeIn,
    BalanceOut,
    ClockIn,
    ClockOut,
    ErrorOut,
    LenderApplicationIn,
    LenderReceiptOut,
    MessageSendIn,
    PaymentOut,
    PaymentScheduleIn,
    StatementOut,
)
from contracts.testing.builders import make_action, make_forecast_inputs_tiny, make_readiness_inputs
from contracts.testing.fakes import FAKES_BY_PROTOCOL, FakeAppFacade, make_fake_ticket

MODULES = [
    contracts.actions,
    contracts.dto_books,
    contracts.dto_planning,
    contracts.dto_financing,
    contracts.dto_outputs,
    contracts.dto_agent,
    contracts.dto_app,
    contracts.sandbox_api,
    contracts.seed,
]
TS = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)


def _all_dto_classes() -> set[type[BaseModel]]:
    out = set()
    for mod in MODULES:
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if issubclass(obj, Frozen) and obj is not Frozen and obj.__module__ == mod.__name__:
                out.add(obj)
    return out


def _walk(value: Any, seen: set[type], out: list[BaseModel]) -> None:
    if isinstance(value, BaseModel):
        if type(value) not in seen:
            seen.add(type(value))
        out.append(value)
        for name in type(value).model_fields:
            _walk(getattr(value, name), seen, out)
    elif isinstance(value, dict):
        for v in value.values():
            _walk(v, seen, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, seen, out)


def _samples(tmp_path: Path) -> list[Any]:
    app = FakeAppFacade(out_dir=tmp_path)
    drafts = app.draft_reminders()
    proposal = app.propose_send(drafts[0].draft_id)
    token_id = app.approve(proposal.proposal_id)
    executed = app.execute(proposal.proposal_id)
    pack = app.build_loan_pack(70000, 30, "Working capital")
    action = make_action(
        ActionType.SCHEDULE_SUPPLIER_PAYMENT,
        {
            "bill_id": "b",
            "payee_id": "p",
            "amount_cents": 1,
            "value_date": "2026-10-14",
            "bank_account_ref": "SBX-AA-00000001",
            "from_account_id": "acc_operating",
        },
        provenance=[ProvenanceRef(source=SourceKind.BILL, ref_id="b", field="x", trusted=False)],
    )
    view = app.books_view()
    return [
        app.dashboard(),
        app.forecast(),
        app.cash_plan(),
        app.financing(),
        app.readiness(),
        view["pnl"],
        view["cash_flow"],
        view["balance_sheet"],
        view["reconciliation"],
        app.receivables(),
        app.pending(),
        proposal,
        executed,
        pack,
        app.ports.bank.list_accounts(),
        app.ports.bank.list_payees(),
        app.ports.bank.list_transactions("acc_operating", date(2026, 1, 1), date(2026, 12, 31)),
        app.ports.bank.list_payments(),
        app.ports.messaging.list_messages(),
        list(app.ports.lender.applications.values()),
        app.bundle,
        app.fin.catalogue(),
        app.tier_table(),
        app.controls_table(),
        app.cap_usage(),
        app.audit_rows(),
        app.confirm_queue(),
        app.chat("how is my cash"),
        action,
        make_fake_ticket(action),
        app.capture_receipt(tmp_path / "r.png"),
        make_forecast_inputs_tiny(crunch=True),
        make_readiness_inputs(),
        app.outputs.reminder_facts(
            app.books.invoice("inv_tiny_1"),
            app.books.counterparty("cpt_tiny_catering"),
            date(2026, 10, 14),
        ),
        app.outputs.delay_facts(
            app.books.bill("bil_tiny_2"),
            app.books.counterparty("cpt_tiny_mills"),
            date(2026, 10, 30),
        ),
        ApprovalToken(
            token_id=token_id,
            proposal_id=proposal.proposal_id,
            params_hash="h",
            approved_by=OWNER,
            approved_at=TS,
            expires_at=TS,
        ),
        ApprovalSummary(
            proposal_id="p",
            action_type=ActionType.SEND_REMINDER,
            tier=Tier.T2_APPROVE,
            amount_cents=1,
            currency="USD",
            counterparty_name=None,
            payee_account_masked=None,
            reason="r",
            sandbox_target="t",
            cap_used_today_cents=0,
            cap_per_day_cents=1,
            cap_per_action_cents=1,
            warnings=[],
            expires_at=None,
        ),
        ExecutionResult(action_id="a", ok=True, sbx_ref=None, result={}),
        AuditRecord.model_validate(app.audit_rows()[0].model_dump()),
        CategorisationResult(
            raw_txn_id="r",
            label="5000",
            confidence=Decimal("0.9"),
            method=CategorisationMethod.RULE,
            needs_confirmation=False,
            rule_id="R-FLOUR",
        ),
        JournalEntryDTO(
            id="je_1",
            date=date(2026, 10, 1),
            memo="m",
            source=EntrySource.BANK,
            source_ref="s",
            status=EntryStatus.POSTED,
            created_by="system",
            reversal_of=None,
            lines=[JournalLineDTO(account_code="1000", debit_cents=1, credit_cents=0)],
        ),
        LLMRequest(
            task=LLMTask.EXPLAIN_FORECAST,
            payload={"week": "3"},
            data_class=DataClass.C1_AGGREGATE,
            output_schema={},
        ),
        LLMResponse(
            task=LLMTask.EXPLAIN_FORECAST,
            backend=LLMBackend.MOCK,
            model="mock",
            output={},
            cached=False,
            fallback_used=False,
            latency_ms=0,
        ),
        RouterResult(
            intent=Intent.HELP, confidence=Decimal("1"), method="rule", params={}, missing=[]
        ),
        ChatReply(text="t", ai_label=None, intent=Intent.HELP, attachments=[], proposals=[]),
        DemoStepResult(step=1, title="Capture", ok=True, details={}),
        BalanceOut(account_id="a", balance_cents=1, available_cents=1, today=date(2026, 10, 14)),
        StatementOut(
            account_id="a", month="2026-10", opening_cents=0, closing_cents=0, transactions=[]
        ),
        AdminPayeeIn(id="p", name="n", bank_account_ref="SBX-AA-00000001"),
        ClockIn(today=date(2026, 10, 14)),
        ClockOut(today=date(2026, 10, 14)),
        ErrorOut(code="UNAUTHENTICATED", message="m"),
        AGENT,
        CHART_OF_ACCOUNTS[0],
        drafts,
        list(app.gate.proposals.values()),
        app.books.ingest_bank(app.bundle.bank_transactions[:2]),
        DriverItem(
            kind="outflow",
            ref_id="bil_1",
            label="Flour",
            amount_cents=1,
            week=3,
            expected_date=date(2026, 10, 30),
        ),
        EventUplift(
            name="Festival", start=date(2026, 11, 5), end=date(2026, 11, 11), factor=Decimal("1.30")
        ),
        MessageSendIn(channel="EMAIL", to_ref="a@b.test", subject="s", body="b"),
        PaymentScheduleIn(
            from_account_id="acc_operating",
            payee_id="p",
            bank_account_ref="SBX-AA-00000001",
            amount_cents=1,
            value_date=date(2026, 10, 14),
            reference="r",
        ),
        PaymentOut(
            sbx_ref="pay_1",
            from_account_id="acc_operating",
            payee_id="p",
            amount_cents=1,
            value_date=date(2026, 10, 14),
            reference="r",
            status="settled",
            failure_code=None,
            settled_on=date(2026, 10, 14),
            created_at=TS,
        ),
        LenderApplicationIn(
            lender_id="lender_demo",
            business_name="b",
            requested_amount_cents=1,
            term_days=30,
            purpose="p",
            pack_sha256="h",
            sections=["cover"],
            pdf_base64="JVBERi0=",
        ),
        LenderReceiptOut(
            receipt_id="rcv_1",
            status="received_under_review",
            pack_sha256="h",
            received_at=TS,
            missing_sections=[],
        ),
        LoanPackContent(
            business_name="Tiny Bakery",
            as_of=date(2026, 10, 14),
            framework=Framework.IFRS_SME,
            requested_amount_cents=70000,
            term_days=30,
            purpose="p",
            pnl=view["pnl"],
            cash_flow=view["cash_flow"],
            balance_sheet=view["balance_sheet"],
            forecast=app.forecast(),
            reconciliation=view["reconciliation"],
            ageing=app.receivables(),
            weekly_sales=[],
            readiness=app.readiness(),
            financing=None,
            going_concern_note=None,
            accounting_policies=["Accrual basis."],
            audit_final_hash="h",
            audit_chain_ok=True,
        ),
    ]


def test_every_dto_round_trips(tmp_path: Path) -> None:
    """T-CON-02 proves CON-11..CON-21: every DTO class is covered and survives
    model_dump_json -> model_validate_json unchanged."""
    seen: set[type] = set()
    instances: list[BaseModel] = []
    _walk(_samples(tmp_path), seen, instances)
    missing = sorted(c.__name__ for c in _all_dto_classes() - seen)
    assert missing == [], f"DTO classes without a round-trip sample: {missing}"
    for inst in instances:
        back = type(inst).model_validate_json(inst.model_dump_json())
        assert back == inst, type(inst).__name__


def test_dtos_are_frozen_and_strict() -> None:
    """T-CON-02 proves CON-08: frozen models with extra='forbid'."""
    for cls in _all_dto_classes():
        assert cls.model_config.get("frozen") is True, cls.__name__
        assert cls.model_config.get("extra") == "forbid", cls.__name__
    with pytest.raises(Exception):  # noqa: B017
        OWNER.id = "x"  # type: ignore[misc]


def _protocols() -> dict[str, type]:
    out = {}
    for mod in (services_mod, ports_mod):
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if getattr(obj, "_is_protocol", False) and obj.__module__ == mod.__name__:
                out[name] = obj
    return out


@pytest.mark.parametrize("name", sorted(_protocols()))
def test_fake_satisfies_protocol(name: str, tmp_path: Path) -> None:
    """T-CON-02 proves CON-22: each Protocol has a fake, and isinstance holds."""
    protocol = _protocols()[name]
    factory = FAKES_BY_PROTOCOL.get(name)
    assert factory is not None, f"no fake for {name}"
    fake = factory()
    assert isinstance(fake, protocol), name
    for attr, member in inspect.getmembers(protocol, inspect.isfunction):
        if not attr.startswith("_"):
            sig_p = inspect.signature(member)
            sig_f = inspect.signature(getattr(fake, attr))
            assert list(sig_p.parameters)[1:] == list(sig_f.parameters), (
                f"{name}.{attr} signature differs"
            )
