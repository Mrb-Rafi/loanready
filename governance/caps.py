"""Caps (PRD/03 §3.4; CTL-LIM-01..05).

Usage is computed from the proposals table: pending, approved and executed proposals all
count, so two pending approvals cannot together exceed a cap. Windows:

- per action: ``amount_cents`` of the money action itself;
- per day: money actions with the same value date (today when the action has none);
- per payee per week: same payee, value dates within 6 days either side;
- messages per customer per week: sends to the same ``to_ref`` created in the last 7 days;
- reminder rate: one SEND_REMINDER per invoice per 7 days.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from contracts.actions import Action, Proposal
from contracts.dto_app import CapUsage
from contracts.enums import ActionType, ProposalStatus, ReasonCode
from contracts.errors import CapExceeded
from governance.tiers import MESSAGE_ACTIONS, MONEY_ACTIONS

COUNTED = (ProposalStatus.PENDING_APPROVAL, ProposalStatus.APPROVED, ProposalStatus.EXECUTED)
WEEK = timedelta(days=7)


def _value_date(params: dict[str, Any], today: date) -> date:
    v = params.get("value_date")
    if v is None:
        return today
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def _amount(params: dict[str, Any]) -> int:
    return int(params.get("amount_cents") or 0)


def check_caps(
    action: Action,
    others: list[Proposal],
    limits: dict[str, int],
    today: date,
    now: datetime,
) -> list[str]:
    """Raise CapExceeded with the first cap broken; return the CTL ids exercised.

    ``others`` are counted proposals, excluding the one being checked."""
    p = action.params
    ctl: list[str] = []
    if action.type in MONEY_ACTIONS:
        amount = _amount(p)
        if amount > limits["cap_per_action_cents"]:
            raise CapExceeded(
                "amount above the per-action cap", reason_code=ReasonCode.CAP_PER_ACTION
            )
        vd = _value_date(p, today)
        money = [o for o in others if o.action.type in MONEY_ACTIONS]
        day_total = sum(
            _amount(o.action.params) for o in money if _value_date(o.action.params, today) == vd
        )
        if day_total + amount > limits["cap_per_day_cents"]:
            raise CapExceeded("daily cap reached", reason_code=ReasonCode.CAP_PER_DAY)
        payee = p.get("payee_id")
        if payee:
            week_total = sum(
                _amount(o.action.params)
                for o in money
                if o.action.params.get("payee_id") == payee
                and abs((_value_date(o.action.params, today) - vd).days) <= 6
            )
            if week_total + amount > limits["cap_per_payee_week_cents"]:
                raise CapExceeded(
                    "weekly cap for this payee reached", reason_code=ReasonCode.CAP_PER_PAYEE_WEEK
                )
        ctl += ["CTL-LIM-01", "CTL-LIM-02", "CTL-LIM-03"]
    if action.type in MESSAGE_ACTIONS:
        recent = [
            o for o in others if o.action.type in MESSAGE_ACTIONS and now - o.created_at < WEEK
        ]
        to_same = [o for o in recent if o.action.params.get("to_ref") == p.get("to_ref")]
        if len(to_same) + 1 > limits["cap_msgs_per_customer_week"]:
            raise CapExceeded(
                "message cap for this recipient reached",
                reason_code=ReasonCode.CAP_MSGS_PER_CUSTOMER,
            )
        ctl.append("CTL-LIM-04")
        if action.type == ActionType.SEND_REMINDER:
            if any(
                o.action.type == ActionType.SEND_REMINDER
                and o.action.params.get("invoice_id") == p.get("invoice_id")
                for o in recent
            ):
                raise CapExceeded(
                    "a reminder for this invoice was already sent in the last 7 days",
                    reason_code=ReasonCode.RATE_LIMIT_REMINDER,
                )
            ctl.append("CTL-LIM-05")
    return ctl


def usage(others: list[Proposal], limits: dict[str, int], today: date) -> CapUsage:
    """Cap usage for the approval dialog: money actions valued today."""
    used = sum(
        _amount(o.action.params)
        for o in others
        if o.action.type in MONEY_ACTIONS and _value_date(o.action.params, today) == today
    )
    return CapUsage(
        per_action_cents=limits["cap_per_action_cents"],
        per_day_cents=limits["cap_per_day_cents"],
        used_today_cents=used,
        per_payee_week_cents=limits["cap_per_payee_week_cents"],
        msgs_per_customer_week=limits["cap_msgs_per_customer_week"],
    )
