"""T-OUT-01 proves OUT-01, OUT-02: tone boundaries 1/7/8/21/22; not overdue raises;
delay requests only within grace and for penalty-free, non-essential bills."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from contracts.enums import Channel, ReasonCode, Tone
from contracts.errors import InvalidParams
from contracts.testing.builders import make_bill, make_counterparty
from outputs.facts import date_str, delay_facts, reminder_facts, tone_for

AS_OF = date(2026, 10, 14)


@pytest.mark.parametrize(
    ("days", "tone"),
    [(1, Tone.FRIENDLY), (7, Tone.FRIENDLY), (8, Tone.FIRM), (21, Tone.FIRM), (22, Tone.FINAL)],
)
def test_tone_boundaries(days: int, tone: Tone, invoice: Any, customer: Any) -> None:
    """T-OUT-01: FRIENDLY 1-7, FIRM 8-21, FINAL 22+."""
    inv = invoice.model_copy(update={"due_date": AS_OF - timedelta(days=days)})
    f = reminder_facts(inv, customer, AS_OF, "Tiny Bakery")
    assert (f.days_overdue, f.tone) == (days, tone) == (days, tone_for(days))


def test_facts_values(invoice: Any, customer: Any) -> None:
    """T-OUT-01: numbers from code; contact from the stored customer record."""
    f = reminder_facts(invoice, customer, AS_OF, "Tiny Bakery")
    assert (f.amount_str, f.due_date_str, f.invoice_number) == ("$600.00", "09 Oct 2026", "INV-1")
    assert (f.to_ref, f.channel) == ("ap@tiny-catering.test", Channel.EMAIL)
    sms = reminder_facts(
        invoice, customer.model_copy(update={"contact_ref": "+9995550101"}), AS_OF, "B"
    )
    assert sms.channel == Channel.SMS
    assert date_str(date(2026, 1, 5)) == "05 Jan 2026"


def test_not_overdue_raises(invoice: Any, customer: Any) -> None:
    """T-OUT-01: due today or later -> InvalidParams(NOT_OVERDUE)."""
    with pytest.raises(InvalidParams) as err:
        reminder_facts(invoice.model_copy(update={"due_date": AS_OF}), customer, AS_OF, "B")
    assert err.value.reason_code == ReasonCode.NOT_OVERDUE
    with pytest.raises(InvalidParams):
        reminder_facts(invoice, customer.model_copy(update={"contact_ref": None}), AS_OF, "B")


def test_delay_facts_rules() -> None:
    """T-OUT-01 / OUT-02: penalty-free, non-essential, proposed within due + grace."""
    supplier = make_counterparty(id="cpt_tiny_mills", name="Tiny Mills", contact_ref="b@m.test")
    bill = make_bill(late_penalty=False, essential=False, grace_days=14)
    ok = delay_facts(bill, supplier, bill.due_date + timedelta(days=14), "Tiny Bakery")
    assert ok.proposed_date_str == date_str(bill.due_date + timedelta(days=14))
    for bad in (
        bill.model_copy(update={"late_penalty": True}),
        bill.model_copy(update={"essential": True}),
    ):
        with pytest.raises(InvalidParams):
            delay_facts(bad, supplier, bill.due_date + timedelta(days=1), "B")
    with pytest.raises(InvalidParams):
        delay_facts(bill, supplier, bill.due_date + timedelta(days=15), "B")
    with pytest.raises(InvalidParams):
        delay_facts(bill, supplier, bill.due_date, "B")
