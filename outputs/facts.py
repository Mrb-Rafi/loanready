"""Facts for reminders and supplier delay requests (PRD/08 §2, OUT-01, OUT-02).

Every number and date in a message comes from here, computed by code. ``to_ref`` is
always the counterparty's stored contact, never text from a document.
"""

from __future__ import annotations

from datetime import date, timedelta

from contracts.dto_books import BillDTO, CounterpartyDTO, InvoiceDTO
from contracts.dto_outputs import DelayFacts, ReminderFacts
from contracts.enums import Channel, ReasonCode, Tone
from contracts.errors import InvalidParams
from contracts.money import fmt

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def date_str(d: date) -> str:
    """ "14 Oct 2026" with a zero-padded day ("%d %b %Y"), independent of the OS locale."""
    return f"{d.day:02d} {_MONTHS[d.month - 1]} {d.year}"


def tone_for(days_overdue: int) -> Tone:
    """FRIENDLY 1-7 days, FIRM 8-21, FINAL 22+."""
    if days_overdue <= 7:
        return Tone.FRIENDLY
    if days_overdue <= 21:
        return Tone.FIRM
    return Tone.FINAL


def channel_for(to_ref: str) -> Channel:
    return Channel.SMS if to_ref.startswith("+") else Channel.EMAIL


def reminder_facts(
    invoice: InvoiceDTO, customer: CounterpartyDTO, as_of: date, business_name: str
) -> ReminderFacts:
    """OUT-01. days_overdue = (as_of - due_date).days; raises InvalidParams (NOT_OVERDUE)
    unless it is at least 1, and when the customer has no stored contact."""
    days = (as_of - invoice.due_date).days
    if days < 1:
        raise InvalidParams("invoice is not overdue", reason_code=ReasonCode.NOT_OVERDUE)
    if invoice.outstanding_cents <= 0:
        raise InvalidParams("invoice has nothing outstanding")
    if customer.id != invoice.customer_id:
        raise InvalidParams("customer does not match the invoice")
    if not customer.contact_ref:
        raise InvalidParams("customer has no stored contact")
    return ReminderFacts(
        invoice_id=invoice.id,
        invoice_number=invoice.number,
        customer_name=customer.name,
        to_ref=customer.contact_ref,
        channel=channel_for(customer.contact_ref),
        amount_outstanding_cents=invoice.outstanding_cents,
        amount_str=fmt(invoice.outstanding_cents),
        currency="USD",
        due_date=invoice.due_date,
        due_date_str=date_str(invoice.due_date),
        days_overdue=days,
        tone=tone_for(days),
        business_name=business_name,
    )


def delay_facts(
    bill: BillDTO, supplier: CounterpartyDTO, proposed: date, business_name: str
) -> DelayFacts:
    """OUT-02. Only for bills with no late penalty, not essential, and a proposed date
    after the due date and within ``due_date + grace_days``."""
    if bill.late_penalty or bill.essential:
        raise InvalidParams("bill has a late penalty or is essential; it cannot be delayed")
    if not bill.due_date < proposed <= bill.due_date + timedelta(days=bill.grace_days):
        raise InvalidParams("proposed date must be after the due date and within the grace period")
    if supplier.id != bill.supplier_id:
        raise InvalidParams("supplier does not match the bill")
    if not supplier.contact_ref:
        raise InvalidParams("supplier has no stored contact")
    return DelayFacts(
        bill_id=bill.id,
        bill_number=bill.number,
        supplier_name=supplier.name,
        to_ref=supplier.contact_ref,
        channel=channel_for(supplier.contact_ref),
        amount_cents=bill.outstanding_cents,
        amount_str=fmt(bill.outstanding_cents),
        due_date=bill.due_date,
        proposed_date=proposed,
        due_date_str=date_str(bill.due_date),
        proposed_date_str=date_str(proposed),
        business_name=business_name,
    )
