"""Financing comparison and lender-readiness score (WP5, PRD/07)."""

REQUIRES_CONTRACTS = "1"

from financing.service import Financing, Readiness  # noqa: E402

__all__ = ["REQUIRES_CONTRACTS", "Financing", "Readiness"]
