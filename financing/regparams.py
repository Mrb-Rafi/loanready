"""Regulatory thresholds and parameters from ``data/regulatory_params.json`` (PRD/07 §2).

Every rate is a ``Decimal`` parsed from a string. The 1-year LPR is ``None`` until the team
fills in the official published value; the China private-lending cap check is then skipped
with a notice (never an invented value, REG-LND-07).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from contracts._model import Frozen
from contracts.errors import InvalidParams
from infra.paths import DATA_DIR

REGPARAMS_PATH = DATA_DIR / "regulatory_params.json"


class ChinaLendingCap(Frozen):
    multiple_of_lpr: Decimal
    lpr_1y: Decimal | None  # None while the file still holds the placeholder
    note: str


class RegParams(Frozen):
    as_of: date
    ecl_provision_matrix: dict[str, Decimal]
    high_cost_flag_apr: Decimal
    china_private_lending_cap: ChinaLendingCap
    dscr_comfort: Decimal
    disclosure_regimes: list[str]

    @property
    def cn_cap_apr(self) -> Decimal | None:
        """Cap on nominal APR = multiple x 1-year LPR, or ``None`` when the LPR is not set."""
        cap = self.china_private_lending_cap
        if cap.lpr_1y is None:
            return None
        return cap.multiple_of_lpr * cap.lpr_1y


def _dec(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise InvalidParams(f"{field} must be a Decimal string", details={"field": field})
    try:
        d = Decimal(value)
    except InvalidOperation as exc:
        raise InvalidParams(
            f"{field} is not a decimal: {value!r}", details={"field": field}
        ) from exc
    if not d.is_finite():
        raise InvalidParams(f"{field} must be finite", details={"field": field})
    return d


def _optional_dec(value: object, field: str) -> Decimal | None:
    """A Decimal string, or ``None`` for the placeholder / empty value."""
    if value is None or (
        isinstance(value, str) and (not value.strip() or value.startswith("PLACEHOLDER"))
    ):
        return None
    d = _dec(value, field)
    return d if d > 0 else None


def parse(raw: dict[str, object]) -> RegParams:
    """Validate the raw JSON object; unknown keys or invalid Decimal strings raise."""
    if not isinstance(raw, dict):
        raise InvalidParams("regulatory params must be a JSON object")
    unknown = sorted(set(raw) - set(RegParams.model_fields))
    if unknown:
        raise InvalidParams(f"unknown regulatory params keys: {unknown}")
    cap_raw = raw.get("china_private_lending_cap")
    if not isinstance(cap_raw, dict):
        raise InvalidParams("china_private_lending_cap must be an object")
    matrix_raw = raw.get("ecl_provision_matrix")
    if not isinstance(matrix_raw, dict):
        raise InvalidParams("ecl_provision_matrix must be an object")
    try:
        return RegParams(
            as_of=raw.get("as_of"),  # type: ignore[arg-type]
            ecl_provision_matrix={
                str(k): _dec(v, f"ecl_provision_matrix.{k}") for k, v in matrix_raw.items()
            },
            high_cost_flag_apr=_dec(raw.get("high_cost_flag_apr"), "high_cost_flag_apr"),
            china_private_lending_cap=ChinaLendingCap(
                multiple_of_lpr=_dec(cap_raw.get("multiple_of_lpr"), "multiple_of_lpr"),
                lpr_1y=_optional_dec(cap_raw.get("lpr_1y"), "lpr_1y"),
                note=str(cap_raw.get("note", "")),
            ),
            dscr_comfort=_dec(raw.get("dscr_comfort"), "dscr_comfort"),
            disclosure_regimes=list(raw.get("disclosure_regimes", [])),  # type: ignore[call-overload]
        )
    except InvalidParams:
        raise
    except Exception as exc:  # pydantic ValidationError and friends
        raise InvalidParams(f"invalid regulatory params: {exc}") from exc


def load(path: Path | None = None) -> RegParams:
    """Load and validate ``data/regulatory_params.json`` (or ``path``)."""
    p = path or REGPARAMS_PATH
    with p.open(encoding="utf-8") as fh:
        return parse(json.load(fh))
