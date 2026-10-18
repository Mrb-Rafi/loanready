"""T-GOV-17 / T-GOV-18: execution tickets and the mint-only-in-gate rule."""

from __future__ import annotations

import ast
from typing import Any

import pytest

from contracts.actions import OWNER, binding_for
from contracts.enums import ActionType
from contracts.errors import TicketInvalid
from contracts.ids import get_id_factory
from governance import tickets
from infra.paths import REPO_ROOT


def _ticket(gov: Any, action: Any) -> Any:
    return tickets._mint(action, gov.gate._repo, gov.clock, get_id_factory())


def test_ticket_verification(gov: Any) -> None:
    """T-GOV-17 proves GOV-10, CTL-AC-06: valid passes once; bad signature, expired, reused,
    wrong type and binding mismatch each raise TicketInvalid."""
    verify = gov.gate.ticket_verifier()
    action = gov.pay()
    binding = binding_for(action.type, action.params)
    t = _ticket(gov, action)
    assert set(t.binding) == {
        "payee_id",
        "bank_account_ref",
        "amount_cents",
        "value_date",
        "from_account_id",
    }
    assert (t.expires_at - t.issued_at).total_seconds() == 60
    verify(t, ActionType.SCHEDULE_SUPPLIER_PAYMENT, binding)
    with pytest.raises(TicketInvalid, match="already used"):
        verify(t, ActionType.SCHEDULE_SUPPLIER_PAYMENT, binding)

    t2 = _ticket(gov, action)
    forged = t2.model_copy(update={"binding": {**t2.binding, "amount_cents": "1"}})
    with pytest.raises(TicketInvalid, match="signature"):
        verify(forged, ActionType.SCHEDULE_SUPPLIER_PAYMENT, {**binding, "amount_cents": "1"})
    with pytest.raises(TicketInvalid, match="different action type"):
        verify(t2, ActionType.SEND_REMINDER, binding)
    for field, value in (
        ("amount_cents", "90000"),
        ("bank_account_ref", "SBX-XX-99999999"),
        ("payee_id", "pye_other"),
        ("value_date", "2026-10-15"),
    ):
        with pytest.raises(TicketInvalid, match="does not match"):
            verify(t2, ActionType.SCHEDULE_SUPPLIER_PAYMENT, {**binding, field: value})
    with pytest.raises(TicketInvalid, match="does not match"):
        verify(
            t2,
            ActionType.SCHEDULE_SUPPLIER_PAYMENT,
            {k: v for k, v in binding.items() if k != "value_date"},
        )

    t3 = _ticket(gov, action)
    gov.clock.advance(61)
    with pytest.raises(TicketInvalid, match="expired"):
        verify(t3, ActionType.SCHEDULE_SUPPLIER_PAYMENT, binding)


def test_unregistered_ticket_refused(gov: Any) -> None:
    """GOV-10: a correctly signed ticket that was never registered is refused."""
    action = gov.pay()
    t = _ticket(gov, action)
    other_repo = type(gov.gate._repo)(gov.engine)
    other_repo.use_ticket(t.ticket_id, gov.clock.now())  # consumed elsewhere
    with pytest.raises(TicketInvalid, match="unknown or already used"):
        gov.gate.ticket_verifier()(
            t, ActionType.SCHEDULE_SUPPLIER_PAYMENT, binding_for(action.type, action.params)
        )


def test_executor_receives_valid_ticket(gov: Any) -> None:
    """CTL-AC-06: the ticket handed to the executor verifies against the action's binding."""
    d = gov.gate.evaluate(gov.reminder())
    gov.approve_execute(d, OWNER)
    action, ticket = gov.executor.calls[-1]
    gov.gate.ticket_verifier()(ticket, action.type, binding_for(action.type, action.params))


def test_mint_referenced_only_in_gate() -> None:
    """T-GOV-18 proves GOV-11: ``_mint`` is defined in tickets.py and used only in gate.py
    (tests excepted)."""
    from infra.integration_check import CODE_DIRS, py_files

    users = []
    for path in py_files(REPO_ROOT, CODE_DIRS):
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            name = (
                node.attr
                if isinstance(node, ast.Attribute)
                else node.id
                if isinstance(node, ast.Name)
                else None
            )
            if name == "_mint":
                users.append(rel)
            if isinstance(node, ast.FunctionDef) and node.name == "_mint":
                users.append(f"def:{rel}")
    assert set(users) == {"governance/gate.py", "def:governance/tickets.py"}, users


def test_key_never_exposed(gov: Any) -> None:
    """GOV-09: the HMAC key is not in the ticket, the log or any public attribute."""
    d = gov.gate.evaluate(gov.pay())
    gov.approve_execute(d, OWNER)
    key_hex = tickets._KEY.hex()
    _, ticket = gov.executor.calls[-1]
    assert key_hex not in ticket.model_dump_json()
    assert all(key_hex not in r.model_dump_json() for r in gov.audit.rows())
    assert not [n for n in dir(tickets) if "key" in n.lower() and not n.startswith("_")]
