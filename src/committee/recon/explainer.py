"""Auto-explanation pass: suggests a cause for a recon break.

Runs before any break is written to the database.  The returned cause is a
hint only — it never auto-resolves anything except via the materiality path
in the engine.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import MarketObservation

IMPORT_GAP = "import_gap"
MISSED_REINVEST = "missed_reinvest"
SPLIT = "split"
UNRECORDED_TRANSFER = "unrecorded_transfer"

# Tolerance for integer-ratio detection: within 2% of t0_qty
_SPLIT_RATIO_TOLERANCE_PCT = Decimal("0.02")


def suggest_cause(
    session: Session,
    instrument_id: int,
    account_id: str,
    t0_date: date,
    t1_date: date,
    t0_qty: Decimal,
    expected_qty: Decimal,
    actual_qty: Decimal,
    delta: Decimal,
    coverage_gap: bool,
) -> str | None:
    """Return a suggested cause string or None (unknown)."""

    if coverage_gap:
        return IMPORT_GAP

    # Forward-split detection: actual_qty ≈ n × t0_qty for integer n ≥ 2.
    # This fires when a split happened but was NOT recorded as an InstrumentEvent,
    # so the projector couldn't account for it.
    if t0_qty > 0 and delta > 0:
        ratio = actual_qty / t0_qty
        nearest_int = round(ratio)
        if nearest_int >= 2:
            tol = t0_qty * _SPLIT_RATIO_TOLERANCE_PCT
            if abs(actual_qty - Decimal(nearest_int) * t0_qty) <= tol:
                return SPLIT

    # Missed reinvestment: positive delta and there's a dividend for this
    # instrument in the window (suggesting shares were reinvested but the
    # reinvest transaction was never imported).
    if delta > 0:
        has_dividend = bool(
            session.execute(
                select(MarketObservation.id)
                .where(
                    MarketObservation.instrument_id == instrument_id,
                    MarketObservation.unit == "USD_dividend",
                    MarketObservation.observed_date > t0_date,
                    MarketObservation.observed_date <= t1_date,
                )
                .limit(1)
            ).scalar_one_or_none()
        )
        if has_dividend:
            return MISSED_REINVEST

    # Default: unexplained delta (in or out) — most likely a transfer or
    # a corporate action not yet in the data.
    return UNRECORDED_TRANSFER
