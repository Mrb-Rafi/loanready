"""T-PLN-05 proves PRD/06 §2: later-of logic for overdue invoices, > 60 days excluded,
no-history defaults, lateness clamped at 0."""

from __future__ import annotations

from datetime import date, timedelta

from contracts.dto_planning import InvoiceForecastItem
from contracts.enums import Scenario
from planning.scenarios import collection_date

AS_OF = date(2026, 10, 14)


def inv(due: date, hist: list[int]) -> InvoiceForecastItem:
    return InvoiceForecastItem(
        invoice_id="i",
        customer_id="c",
        due_date=due,
        outstanding_cents=100,
        lateness_history_days=hist,
    )


def test_tiny_inv1() -> None:
    """T-PLN-05: INV-1 (due 10-09, history 10/20/30): BASE 10-29, LOW 11-11, HIGH 10-17."""
    i = inv(date(2026, 10, 9), [10, 20, 30])
    assert collection_date(i, Scenario.BASE, AS_OF) == date(2026, 10, 29)
    assert collection_date(i, Scenario.LOW, AS_OF) == date(2026, 11, 11)
    assert collection_date(i, Scenario.HIGH, AS_OF) == date(2026, 10, 17)


def test_overdue_floor_wins() -> None:
    """T-PLN-05: short lateness -> AS_OF + 14 (BASE) / + 28 (LOW)."""
    i = inv(date(2026, 10, 10), [1, 2, 3])
    assert collection_date(i, Scenario.BASE, AS_OF) == AS_OF + timedelta(days=14)
    assert collection_date(i, Scenario.LOW, AS_OF) == AS_OF + timedelta(days=28)


def test_rina_s3_expected_in_w4() -> None:
    """T-PLN-05 / S3: INV-1043 due 10-04 + median 34 -> 11-07; INV-1051 10-07 + 34 -> 11-10."""
    a = inv(date(2026, 10, 4), [28, 31, 34, 36, 40])
    b = inv(date(2026, 10, 7), [30, 33, 35, 37])
    assert collection_date(a, Scenario.BASE, AS_OF) == date(2026, 11, 7)
    assert collection_date(b, Scenario.BASE, AS_OF) == date(2026, 11, 10)


def test_over_sixty_days() -> None:
    """T-PLN-05 / S9: 75 days overdue -> not collected in LOW and BASE; HIGH AS_OF + 3."""
    i = inv(AS_OF - timedelta(days=75), [0, 5, 45, 70])
    assert collection_date(i, Scenario.LOW, AS_OF) is None
    assert collection_date(i, Scenario.BASE, AS_OF) is None
    assert collection_date(i, Scenario.HIGH, AS_OF) == AS_OF + timedelta(days=3)
    sixty = inv(AS_OF - timedelta(days=60), [])
    assert collection_date(sixty, Scenario.BASE, AS_OF) == AS_OF + timedelta(days=14)


def test_no_history_and_negative_lateness() -> None:
    """T-PLN-05: no history -> LOW 30, BASE 14, HIGH 0 days late; negatives clamp to 0."""
    due = date(2026, 10, 26)
    i = inv(due, [])
    assert collection_date(i, Scenario.LOW, AS_OF) == due + timedelta(days=30)
    assert collection_date(i, Scenario.BASE, AS_OF) == due + timedelta(days=14)
    assert collection_date(i, Scenario.HIGH, AS_OF) == due
    early = inv(due, [-5, -3, -1])
    assert collection_date(early, Scenario.BASE, AS_OF) == due
