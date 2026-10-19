"""T-PLN-01 proves PRD/06 §2: percentile / median (linear method), mean, CV."""

from __future__ import annotations

from decimal import Decimal

import pytest

from planning.stats import coefficient_of_variation, mean, median, percentile


def test_known_values() -> None:
    """T-PLN-01: numpy-linear results on small lists."""
    xs = [100000, 120000, 110000]
    assert percentile(xs, 25) == Decimal(105000)
    assert median(xs) == Decimal(110000)
    assert percentile(xs, 75) == Decimal(115000)
    assert percentile([1, 2, 3, 4], 50) == Decimal("2.5")
    assert percentile([10, 20, 30], 75) == Decimal(25)
    assert percentile([0, 5, 45, 70], 50) == Decimal(25)
    assert percentile([7], 90) == Decimal(7)
    assert percentile([1, 2, 3, 4, 5], 100) == Decimal(5)
    assert percentile([1, 2, 3, 4, 5], 0) == Decimal(1)


def test_mean_cv_and_errors() -> None:
    """T-PLN-01: mean and coefficient of variation; empty input raises."""
    assert mean([2, 4]) == Decimal(3)
    assert coefficient_of_variation([2, 4]) == Decimal(1) / Decimal(3)
    assert coefficient_of_variation([0, 0]) == 0
    with pytest.raises(ValueError):
        percentile([], 50)
    with pytest.raises(ValueError):
        mean([])
