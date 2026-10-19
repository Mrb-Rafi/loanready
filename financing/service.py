"""WP5 services: ``Financing`` (FinancingService) and ``Readiness`` (ReadinessService).

Pure: inputs in, DTOs out, no database access (ARC-26). The catalogue and regulatory
params are read from ``data/`` once, on first use.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from contracts.dto_financing import (
    CatalogueDTO,
    FinancingComparisonDTO,
    FinancingOption,
    FinancingRequest,
    ProductDTO,
    ReadinessDTO,
    ReadinessInputs,
)
from financing import catalogue as catalogue_mod
from financing import compare as compare_mod
from financing import readiness as readiness_mod
from financing import regparams as regparams_mod
from financing.regparams import RegParams


class Financing:
    """Implements ``contracts.services.FinancingService``."""

    def __init__(
        self,
        catalogue_path: Path | None = None,
        regparams_path: Path | None = None,
        *,
        catalogue: CatalogueDTO | None = None,
        params: RegParams | None = None,
    ) -> None:
        self._catalogue_path = catalogue_path
        self._regparams_path = regparams_path
        self._catalogue = catalogue
        self._params = params

    def catalogue(self) -> CatalogueDTO:
        if self._catalogue is None:
            self._catalogue = catalogue_mod.load(self._catalogue_path)
        return self._catalogue

    def params(self) -> RegParams:
        if self._params is None:
            self._params = regparams_mod.load(self._regparams_path)
        return self._params

    def product(self, product_id: str) -> ProductDTO:
        """Raises ``CatalogueMiss`` for an unknown id."""
        return catalogue_mod.get(self.catalogue(), product_id)

    def compare(
        self, request: FinancingRequest, product_ids: Sequence[str] | None = None
    ) -> FinancingComparisonDTO:
        return compare_mod.compare(self.catalogue(), self.params(), request, product_ids)

    def quote(self, request: FinancingRequest, product_id: str) -> FinancingOption:
        return compare_mod.quote(self.catalogue(), self.params(), request, product_id)


class Readiness:
    """Implements ``contracts.services.ReadinessService``."""

    def score(self, inputs: ReadinessInputs) -> ReadinessDTO:
        return readiness_mod.score(inputs)
