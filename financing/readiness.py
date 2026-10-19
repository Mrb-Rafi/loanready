"""Lender-readiness self-assessment score, 0 to 100 (PRD/07 §7, CTL-AI-08).

Deterministic and computed from business data only (``ReadinessInputs`` has no owner age,
gender, nationality, marital status or other personal attributes; FIN-05, REG-AI-01).
Components (points linear between anchors, clamped to [0, weight]):

=============  ======  ==============================================================
key            weight  rule
=============  ======  ==============================================================
history        15      months_of_records: 0 -> 0, 6+ -> 15
bookkeeping    20      10 x categorised_share + 10 x reconciled_share
separation     15      15 x personal_separated_share - 1 per unconfirmed item (min 0)
stability      15      CV of weekly sales (population stdev / mean): <= 0.10 -> 15,
                       >= 0.60 -> 0
cash_health    15      share of the last 12 weeks with operating CF >= 0: 0 -> 0, 1 -> 15
receivables    10      5 x (1 - overdue/receivables) + 5 x max(0, 1 - overdue/monthly_sales)
debt_service   10      DSCR = monthly operating CF / monthly debt service: no debt -> 10;
                       DSCR >= 2.0 -> 10; <= 1.0 -> 0; linear between
=============  ======  ==============================================================

``score = round_half_even(sum(points))``; band LOW < 50, FAIR 50-74, GOOD >= 75.
Improvement gains are computed by re-scoring with the improved input, never estimated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal

from contracts.dto_financing import Improvement, ReadinessComponent, ReadinessDTO, ReadinessInputs
from contracts.enums import ActionType, ReadinessBand
from contracts.money import MONEY_CTX, fmt

DISCLAIMER = (
    "Self-assessment to help prepare a loan application. It is not a credit decision or a "
    "credit score from a lender."
)

_ZERO = Decimal(0)
_ONE = Decimal(1)
_POINTS_Q = Decimal("0.01")
CV_BEST = Decimal("0.10")
CV_WORST = Decimal("0.60")
DSCR_FLOOR = Decimal("1.0")
DSCR_FULL = Decimal("2.0")
HISTORY_FULL_MONTHS = 6
CASH_WEEKS = 12


def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, x))


def _share(x: Decimal) -> Decimal:
    return _clamp(Decimal(x), _ZERO, _ONE)


def _div(a: Decimal | int, b: Decimal | int) -> Decimal:
    return MONEY_CTX.divide(Decimal(a), Decimal(b))


def sales_cv(weekly_sales: list[int]) -> Decimal | None:
    """Coefficient of variation = population stdev / mean; ``None`` if no data or mean <= 0."""
    if not weekly_sales:
        return None
    n = Decimal(len(weekly_sales))
    mean = _div(sum(weekly_sales), n)
    if mean <= 0:
        return None
    dev = [MONEY_CTX.subtract(Decimal(x), mean) for x in weekly_sales]
    var = _div(sum((MONEY_CTX.multiply(d, d) for d in dev), _ZERO), n)
    return _div(MONEY_CTX.sqrt(var), mean)


def dscr(inputs: ReadinessInputs) -> Decimal | None:
    """Debt-service coverage ratio; ``None`` when there is no debt service."""
    if inputs.monthly_debt_service_cents <= 0:
        return None
    return _div(inputs.monthly_operating_cf_cents, inputs.monthly_debt_service_cents)


@dataclass(frozen=True)
class _Spec:
    key: str
    label: str
    weight: int
    rule: str
    points: Callable[[ReadinessInputs], Decimal]
    metric: Callable[[ReadinessInputs], str]


def _p_history(i: ReadinessInputs) -> Decimal:
    return _clamp(_div(15 * max(i.months_of_records, 0), HISTORY_FULL_MONTHS), _ZERO, Decimal(15))


def _p_bookkeeping(i: ReadinessInputs) -> Decimal:
    return 10 * _share(i.categorised_share) + 10 * _share(i.reconciled_share)


def _p_separation(i: ReadinessInputs) -> Decimal:
    return max(_ZERO, 15 * _share(i.personal_separated_share) - max(i.unconfirmed_count, 0))


def _p_stability(i: ReadinessInputs) -> Decimal:
    cv = sales_cv(i.weekly_sales)
    if cv is None:
        return _ZERO
    return _clamp(15 * _div(CV_WORST - cv, CV_WORST - CV_BEST), _ZERO, Decimal(15))


def _cash_weeks(i: ReadinessInputs) -> list[int]:
    return list(i.weekly_operating_cf[-CASH_WEEKS:])


def _p_cash_health(i: ReadinessInputs) -> Decimal:
    weeks = _cash_weeks(i)
    if not weeks:
        return _ZERO
    return 15 * _div(sum(1 for w in weeks if w >= 0), len(weeks))


def _p_receivables(i: ReadinessInputs) -> Decimal:
    overdue = max(i.overdue_receivables_cents, 0)
    if i.receivables_cents > 0:
        a = 5 * _share(_ONE - _div(overdue, i.receivables_cents))
    else:
        a = Decimal(5) if overdue == 0 else _ZERO
    if i.monthly_sales_cents > 0:
        b = 5 * max(_ZERO, _ONE - _div(overdue, i.monthly_sales_cents))
    else:
        b = Decimal(5) if overdue == 0 else _ZERO
    return a + b


def _p_debt_service(i: ReadinessInputs) -> Decimal:
    d = dscr(i)
    if d is None:
        return Decimal(10)
    return _clamp(10 * _div(d - DSCR_FLOOR, DSCR_FULL - DSCR_FLOOR), _ZERO, Decimal(10))


def _pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal(1), rounding=ROUND_HALF_EVEN)}%"


def _m_stability(i: ReadinessInputs) -> str:
    cv = sales_cv(i.weekly_sales)
    return "no sales data" if cv is None else f"CV {cv.quantize(Decimal('0.01'))}"


def _m_debt(i: ReadinessInputs) -> str:
    d = dscr(i)
    return "no debt service" if d is None else f"DSCR {d.quantize(Decimal('0.01'))}"


SPECS: tuple[_Spec, ...] = (
    _Spec(
        "history",
        "Record history",
        15,
        "0 months -> 0, 6+ months -> 15",
        _p_history,
        lambda i: f"{i.months_of_records} months",
    ),
    _Spec(
        "bookkeeping",
        "Bookkeeping quality",
        20,
        "10 x categorised share + 10 x reconciled share",
        _p_bookkeeping,
        lambda i: (
            f"{_pct(_share(i.categorised_share))} categorised, "
            f"{_pct(_share(i.reconciled_share))} reconciled"
        ),
    ),
    _Spec(
        "separation",
        "Personal vs business separation",
        15,
        "15 x separated share - 1 per unconfirmed item (min 0)",
        _p_separation,
        lambda i: (
            f"{_pct(_share(i.personal_separated_share))} separated, "
            f"{i.unconfirmed_count} unconfirmed"
        ),
    ),
    _Spec(
        "stability",
        "Sales stability",
        15,
        "CV of weekly sales <= 0.10 -> 15, >= 0.60 -> 0",
        _p_stability,
        _m_stability,
    ),
    _Spec(
        "cash_health",
        "Cash health",
        15,
        "share of last 12 weeks with operating CF >= 0 x 15",
        _p_cash_health,
        lambda i: (
            f"{sum(1 for w in _cash_weeks(i) if w >= 0)} of {len(_cash_weeks(i))} "
            "weeks non-negative"
        ),
    ),
    _Spec(
        "receivables",
        "Receivables",
        10,
        "5 x (1 - overdue/receivables) + 5 x max(0, 1 - overdue/monthly sales)",
        _p_receivables,
        lambda i: f"{fmt(i.overdue_receivables_cents)} overdue of {fmt(i.receivables_cents)}",
    ),
    _Spec(
        "debt_service",
        "Debt service",
        10,
        "no debt -> 10; DSCR >= 2.0 -> 10, <= 1.0 -> 0",
        _p_debt_service,
        _m_debt,
    ),
)


def _q(x: Decimal) -> Decimal:
    return x.quantize(_POINTS_Q, rounding=ROUND_HALF_EVEN)


def components(inputs: ReadinessInputs) -> list[ReadinessComponent]:
    """Every component with points quantized to 0.01."""
    return [
        ReadinessComponent(
            key=s.key,
            label=s.label,
            weight=s.weight,
            points=_q(s.points(inputs)),
            metric=s.metric(inputs),
            rule=s.rule,
        )
        for s in SPECS
    ]


def total_points(inputs: ReadinessInputs) -> Decimal:
    """Sum of the quantized component points."""
    return sum((c.points for c in components(inputs)), _ZERO)


def band_for(score: int) -> ReadinessBand:
    if score >= 75:
        return ReadinessBand.GOOD
    if score >= 50:
        return ReadinessBand.FAIR
    return ReadinessBand.LOW


def score_value(inputs: ReadinessInputs) -> int:
    """``round_half_even(sum(points))``."""
    return int(total_points(inputs).quantize(_ONE, rounding=ROUND_HALF_EVEN))


def _candidates(i: ReadinessInputs) -> list[tuple[str, str | None, ReadinessInputs]]:
    """(text, hint_action, improved inputs) for every concrete action that could add points."""
    out: list[tuple[str, str | None, ReadinessInputs]] = []

    def upd(**kw: object) -> ReadinessInputs:
        return i.model_copy(update=kw)

    if i.months_of_records < HISTORY_FULL_MONTHS:
        more = HISTORY_FULL_MONTHS - max(i.months_of_records, 0)
        out.append(
            (
                f"Keep records for {more} more month{'s' if more != 1 else ''}",
                None,
                upd(months_of_records=HISTORY_FULL_MONTHS),
            )
        )
    if i.unconfirmed_count > 0:
        n = i.unconfirmed_count
        out.append(
            (
                f"Confirm {n} pending item{'s' if n != 1 else ''}",
                ActionType.CATEGORISE.value,
                upd(unconfirmed_count=0),
            )
        )
    if _share(i.categorised_share) < 1:
        out.append(
            (
                "Categorise the remaining uncategorised transactions",
                ActionType.CATEGORISE.value,
                upd(categorised_share=Decimal(1)),
            )
        )
    if _share(i.reconciled_share) < 1:
        out.append(
            (
                "Reconcile the remaining bank transactions",
                ActionType.RECONCILE.value,
                upd(reconciled_share=Decimal(1)),
            )
        )
    if _share(i.personal_separated_share) < 1:
        out.append(
            (
                "Separate the remaining personal transactions from business ones",
                ActionType.SEPARATE_PERSONAL.value,
                upd(personal_separated_share=Decimal(1)),
            )
        )
    if i.overdue_receivables_cents > 0:
        out.append(
            (
                f"Collect {fmt(i.overdue_receivables_cents)} of overdue invoices",
                ActionType.DRAFT_REMINDER.value,
                upd(overdue_receivables_cents=0),
            )
        )
    negative = sum(1 for w in _cash_weeks(i) if w < 0)
    if negative:
        weeks = list(i.weekly_operating_cf)
        start = max(0, len(weeks) - CASH_WEEKS)
        improved = weeks[:start] + [max(w, 0) for w in weeks[start:]]
        out.append(
            (
                f"Avoid negative operating cash weeks ({negative} of the last "
                f"{len(_cash_weeks(i))})",
                ActionType.PLAN_CASH.value,
                upd(weekly_operating_cf=improved),
            )
        )
    cv = sales_cv(i.weekly_sales)
    if cv is not None and cv > CV_BEST:
        mean = sum(i.weekly_sales) // len(i.weekly_sales)
        out.append(
            (
                f"Steadier weekly sales (variation now {cv.quantize(Decimal('0.01'))}, "
                f"target {CV_BEST})",
                None,
                upd(weekly_sales=[mean] * len(i.weekly_sales)),
            )
        )
    d = dscr(i)
    if d is not None and d < DSCR_FULL:
        target = int(
            (DSCR_FULL * i.monthly_debt_service_cents).to_integral_value(rounding=ROUND_CEILING)
        )
        out.append(
            (
                f"Raise monthly operating cash flow to {fmt(target)} (2.0x debt service)",
                None,
                upd(monthly_operating_cf_cents=max(target, i.monthly_operating_cf_cents)),
            )
        )
    return out


def improvements(inputs: ReadinessInputs) -> list[Improvement]:
    """Concrete actions ranked by recomputed point gain (descending; ties by text)."""
    base = total_points(inputs)
    result: list[Improvement] = []
    for text, hint, improved in _candidates(inputs):
        gain = _q(total_points(improved) - base)
        if gain > 0:
            result.append(
                Improvement(
                    text=f"{text}: +{gain.quantize(Decimal('0.1'))}",
                    points_gain=gain,
                    hint_action=hint,
                )
            )
    result.sort(key=lambda imp: (-imp.points_gain, imp.text))
    return result


def score(inputs: ReadinessInputs) -> ReadinessDTO:
    """The full readiness result for ``inputs``."""
    s = score_value(inputs)
    d = dscr(inputs)
    return ReadinessDTO(
        score=s,
        band=band_for(s),
        components=components(inputs),
        improvements=improvements(inputs),
        dscr=d.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN) if d is not None else None,
        disclaimer=DISCLAIMER,
    )
