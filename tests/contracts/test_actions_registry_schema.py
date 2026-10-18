"""Actions, masking, ticket binding, registry and database schema (WP0)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect as sa_inspect
from sqlmodel import Session, select

from contracts.actions import (
    CSV_COLUMNS,
    INSTRUCTION_FIELDS,
    PARAMS_MODELS,
    TICKET_BINDING_FIELDS,
    AuditRecord,
    binding_for,
    mask_params,
)
from contracts.enums import ActionType, Decision, ReasonCode, SourceKind
from contracts.errors import LoanReadyError, SandboxError
from contracts.registry import (
    CHART_OF_ACCOUNTS,
    CONTROL_IDS,
    EXPECTED_PACKAGES,
    OWNERSHIP,
    SETTINGS_ENV,
    SETTINGS_KEYS,
    SETTINGS_LITERAL_DEFAULTS,
    owner_of,
)
from contracts.schema import APP_METADATA, TABLE_OWNERS, Account, ActionLogRow
from contracts.untrusted import Untrusted


def test_every_action_type_has_params_model() -> None:
    """T-CON-02 proves CON-08: PARAMS_MODELS maps every ActionType; extra fields rejected."""
    assert set(PARAMS_MODELS) == set(ActionType)
    model = PARAMS_MODELS[ActionType.SCHEDULE_SUPPLIER_PAYMENT]
    good = dict(
        bill_id="b",
        payee_id="p",
        amount_cents=90000,
        value_date="2026-10-14",
        bank_account_ref="SBX-GM-00412277",
        from_account_id="acc_operating",
    )
    model.model_validate(good)
    with pytest.raises(ValidationError):
        model.model_validate({**good, "amount_cents": -1})
    with pytest.raises(ValidationError):
        model.model_validate({**good, "extra": 1})
    with pytest.raises(ValidationError):
        PARAMS_MODELS[ActionType.DRAFT_LOAN_PACK].model_validate(
            {"requested_amount_cents": 1, "term_days": 1826, "purpose": "x"}
        )
    PARAMS_MODELS[ActionType.ACCEPT_LOAN].model_validate({"anything": 1})
    assert "amount_cents" in INSTRUCTION_FIELDS and "to_ref" in INSTRUCTION_FIELDS


def test_ticket_binding() -> None:
    """T-CON-02 proves CON-09: binding fields stringified with canonical rules."""
    params = dict(
        bill_id="b",
        payee_id="pye_golden",
        amount_cents=90000,
        value_date=date(2026, 10, 14),
        bank_account_ref="SBX-GM-00412277",
        from_account_id="acc_operating",
    )
    b = binding_for(ActionType.SCHEDULE_SUPPLIER_PAYMENT, params)
    assert b == {
        "payee_id": "pye_golden",
        "bank_account_ref": "SBX-GM-00412277",
        "amount_cents": "90000",
        "value_date": "2026-10-14",
        "from_account_id": "acc_operating",
    }
    assert (
        binding_for(ActionType.SCHEDULE_SUPPLIER_PAYMENT, {**params, "value_date": "2026-10-14"})
        == b
    )
    assert set(TICKET_BINDING_FIELDS) == {
        ActionType.SCHEDULE_SUPPLIER_PAYMENT,
        ActionType.CANCEL_PAYMENT,
        ActionType.SEND_REMINDER,
        ActionType.SEND_DELAY_REQUEST,
        ActionType.SUBMIT_LOAN_PACK,
    }
    assert binding_for(
        ActionType.SEND_REMINDER, {"to_ref": "a@b.test", "body_sha256": "h", "channel": "EMAIL"}
    ) == {"to_ref": "a@b.test", "body_sha256": "h", "channel": "EMAIL"}


def test_mask_params() -> None:
    """T-GOV-21 support, CTL-LOG-04: account refs, contacts, untrusted text and secrets masked."""
    masked = mask_params(
        {
            "bank_account_ref": "SBX-GM-00412277",
            "iban": "GB00TEST12345678",
            "to_ref": "accounts@bright-events.test",
            "description": "d" * 200,
            "memo": Untrusted("m" * 100, SourceKind.BANK, "t"),
            "token_id": "abc",
            "signature": "sig",
            "amount_cents": 90000,
            "nested": {"claimed_bank_account_ref": "SBX-GM-77100938"},
            "phones": None,
        }
    )
    assert masked["bank_account_ref"] == "****2277"
    assert masked["iban"] == "****5678"
    assert masked["to_ref"] == "a***@bright-events.test"
    assert masked["description"] == "d" * 80 + "…"
    assert masked["memo"] == "m" * 80 + "…"
    assert masked["token_id"] == masked["signature"] == "[redacted]"
    assert masked["amount_cents"] == 90000
    assert masked["nested"]["claimed_bank_account_ref"] == "****0938"
    assert mask_params({"to_ref": "+9995550101"})["to_ref"] == "+***01"
    assert list(AuditRecord.model_fields) == CSV_COLUMNS


def test_errors_carry_reason_codes() -> None:
    """T-CON-02 proves CON-07: domain exceptions carry reason codes and details."""
    e = SandboxError(
        "nope", reason_code=ReasonCode.NEW_PAYEE, http_status=403, sandbox_code="PAYEE_UNKNOWN"
    )
    assert (
        isinstance(e, LoanReadyError)
        and e.reason_code == ReasonCode.NEW_PAYEE
        and e.http_status == 403
    )
    assert "NEW_PAYEE" in str(e)


def test_registry() -> None:
    """T-CON-06 support: ownership rules, chart of accounts, settings keys, control ids."""
    assert owner_of("data/rules.json") == "WP3"
    assert owner_of("data/products.json") == "WP5"
    assert owner_of("data/expected.json") == "WP8"
    assert owner_of("contracts/money.py") == "WP0"
    assert owner_of("tests/conftest.py") == "WP0"
    assert owner_of("tests/governance/test_x.py") == "WP1"
    assert owner_of("README.md") == "WP11"
    assert owner_of("PRD/README.md") is None
    assert len(OWNERSHIP) == 12 and {s.wp for s in EXPECTED_PACKAGES.values()} <= set(OWNERSHIP)
    codes = [a.code for a in CHART_OF_ACCOUNTS]
    assert len(codes) == len(set(codes)) == 24
    assert [a.code for a in CHART_OF_ACCOUNTS if a.is_cash] == ["1000", "1010"]
    assert set(SETTINGS_ENV) | set(SETTINGS_LITERAL_DEFAULTS) == set(SETTINGS_KEYS)
    assert len(CONTROL_IDS) == len(set(CONTROL_IDS)) == 57


def test_create_all_and_utc_datetimes(app_db: Any) -> None:
    """T-CON-02 proves CON-17: create_all creates every table; datetimes stored as UTC Z."""
    tables = set(sa_inspect(app_db).get_table_names())
    assert tables == set(APP_METADATA.tables) == set(TABLE_OWNERS)
    assert len(tables) == 20
    ts = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
    with Session(app_db) as s:
        s.add(Account(code="1000", name="Cash at bank", type="ASSET", is_cash=True))
        s.add(
            ActionLogRow(
                seq=1,
                ts=ts,
                action_type="RUN_FORECAST",
                principal_id="agent",
                decision=Decision.ALLOWED.value,
                reason_code="OK",
                prev_hash="0" * 64,
                row_hash="h",
            )
        )
        s.commit()
        row = s.exec(select(ActionLogRow)).one()
        assert row.ts == ts and row.ts.tzinfo is not None
        assert s.exec(select(Account)).one().created_at.tzinfo is not None
    with Session(app_db) as s:
        s.add(
            ActionLogRow(
                seq=2,
                ts=datetime(2026, 10, 14),  # noqa: DTZ001
                action_type="X",
                principal_id="a",  # noqa: DTZ001
                decision="EVENT",
                reason_code="OK",
                prev_hash="h",
                row_hash="h2",
            )
        )
        with pytest.raises(Exception):  # noqa: B017
            s.commit()
