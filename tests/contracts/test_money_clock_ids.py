"""T-CON-01: money, clock and ids."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from contracts.clock import FixedClock, SystemClock, get_clock, month_key, week_of, week_range
from contracts.ids import IdFactory
from contracts.money import fmt, from_cents, parse, pct, q2, sum_cents, to_cents

AS_OF = date(2026, 10, 14)


def test_to_cents_rejects_float_and_bool() -> None:
    """T-CON-01 proves CON-01, PRD-NF-06: floats never enter money."""
    with pytest.raises(TypeError):
        to_cents(1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        to_cents(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sum_cents([100, 2.0])  # type: ignore[list-item]


def test_to_cents_rounds_half_even() -> None:
    """T-CON-01 proves CON-01: ROUND_HALF_EVEN at the cent."""
    assert to_cents(Decimal("0.125")) == 12
    assert to_cents(Decimal("0.135")) == 14
    assert to_cents("1234.565") == 123456
    assert to_cents(12) == 1200
    assert q2(Decimal("2.675")) == Decimal("2.68")


def test_fmt_and_parse() -> None:
    """T-CON-01 proves CON-01: display format and parsing."""
    assert fmt(123456) == "$1,234.56"
    assert fmt(-1200) == "-$12.00"
    assert fmt(5, "EUR") == "€0.05"
    assert fmt(100, "XYZ") == "XYZ 1.00"
    assert parse("$1,234.56") == 123456
    assert parse("1234.56") == 123456
    assert parse("-$12") == -1200
    assert parse("$0.5") == 50
    for bad in ("12.345", "1,23.00", "abc", "", "--1"):
        with pytest.raises(ValueError):
            parse(bad)
    assert from_cents(123456) == Decimal("1234.56")
    assert pct(Decimal("0.19564")) == "19.56%"
    assert pct(Decimal("3.52119"), 1) == "352.1%"


@settings(max_examples=100, deadline=None, derandomize=True)
@given(st.integers(min_value=-(10**12), max_value=10**12))
def test_fmt_parse_round_trip(cents: int) -> None:
    """T-CON-01 proves CON-01: fmt/parse round trip (property)."""
    assert parse(fmt(cents)) == cents
    assert to_cents(from_cents(cents)) == cents


def test_week_ranges() -> None:
    """T-CON-01 proves CON-02: week k = [as_of + 1 + 7(k-1), as_of + 7k]."""
    for k in range(1, 14):
        start, end = week_range(k, AS_OF)
        assert start == AS_OF + timedelta(days=1 + 7 * (k - 1))
        assert end == AS_OF + timedelta(days=7 * k)
        assert week_of(start, AS_OF) == k and week_of(end, AS_OF) == k
    assert week_range(1, AS_OF) == (date(2026, 10, 15), date(2026, 10, 21))
    assert week_range(3, AS_OF) == (date(2026, 10, 29), date(2026, 11, 4))
    assert week_of(AS_OF, AS_OF) is None
    assert week_of(AS_OF + timedelta(days=92), AS_OF) is None
    with pytest.raises(ValueError):
        week_range(14, AS_OF)
    assert month_key(date(2026, 3, 9)) == "2026-03"


def test_fixed_clock_monotonic_and_deterministic() -> None:
    """T-CON-01 proves CON-02: FixedClock starts 09:00 Dhaka (03:00Z) and strictly increases."""
    a, b = FixedClock(AS_OF), FixedClock(AS_OF)
    first = a.now()
    assert first == datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
    seq = [first] + [a.now() for _ in range(20)]
    assert all(x < y for x, y in zip(seq, seq[1:], strict=False))
    assert [b.now() for _ in range(21)] == seq
    a.advance(901)
    assert a.now() - seq[-1] == timedelta(seconds=902)
    assert a.today() == AS_OF
    with pytest.raises(ValueError):
        FixedClock(AS_OF, start=datetime(2026, 10, 14))  # noqa: DTZ001


def test_system_clock_is_aware() -> None:
    """T-CON-01 proves ARC-12: SystemClock keeps AS_OF and gives aware UTC time."""
    c = SystemClock(AS_OF)
    assert c.today() == AS_OF and c.now().tzinfo is not None


def test_get_clock_is_fixed_in_tests() -> None:
    """T-CON-01 proves CON-02: tests run on a FixedClock."""
    assert isinstance(get_clock(), FixedClock)


def test_ids_deterministic_with_seed() -> None:
    """T-CON-01 proves CON-03: seeded ids repeat exactly; format prefix_26hex."""
    a, b = IdFactory(20261014), IdFactory(20261014)
    xs = [a.new("act") for _ in range(5)]
    assert xs == [b.new("act") for _ in range(5)]
    assert all(len(x) == len("act_") + 26 and x.startswith("act_") for x in xs)
    assert len(set(xs)) == 5
    assert IdFactory().new("je") != IdFactory().new("je")
    with pytest.raises(ValueError):
        a.new("Bad Prefix")
