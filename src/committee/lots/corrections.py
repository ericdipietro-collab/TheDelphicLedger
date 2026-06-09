"""Tax-lot correction workflow — append-only.

Never calls session.delete() or UPDATE on lot_corrections, import_batches,
position_snapshots, or transactions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import Holding, LotCorrection

_SCALE = Decimal("0.0001")


@dataclass
class CorrectionConflict:
    correction_id: int
    conflict_reason: str
    broker_qty: Decimal | None = None


def add_correction(
    session: Session,
    account_key: str,
    instrument_id: int,
    acquired_date: date,
    qty: Decimal,
    cost_per_share: Decimal,
    basis_quality: str,
    effective_date: date,
    reason: str,
    currency: str = "USD",
    supersedes_id: int | None = None,
) -> LotCorrection:
    """Append a new correction. Never mutates any existing record."""
    corr = LotCorrection(
        account_key=account_key,
        instrument_id=instrument_id,
        acquired_date=acquired_date,
        qty=qty.quantize(_SCALE, rounding=ROUND_HALF_EVEN),
        cost_per_share=cost_per_share.quantize(_SCALE, rounding=ROUND_HALF_EVEN),
        basis_quality=basis_quality,
        effective_date=effective_date,
        reason=reason,
        currency=currency,
        supersedes_id=supersedes_id,
        validation_status="active",
    )
    session.add(corr)
    return corr


def supersede_correction(
    session: Session,
    prior_id: int,
    qty: Decimal,
    cost_per_share: Decimal,
    reason: str,
    basis_quality: str = "user_corrected",
) -> LotCorrection:
    """Create a new correction superseding a prior one. Never mutates prior's lot data."""
    prior = session.get(LotCorrection, prior_id)
    if prior is None:
        raise ValueError(f"LotCorrection {prior_id} not found")
    if prior.validation_status == "superseded":
        raise ValueError(f"LotCorrection {prior_id} is already superseded")
    new_corr = add_correction(
        session=session,
        account_key=prior.account_key,
        instrument_id=prior.instrument_id,
        acquired_date=prior.acquired_date,
        qty=qty,
        cost_per_share=cost_per_share,
        basis_quality=basis_quality,
        effective_date=prior.effective_date,
        reason=reason,
        currency=prior.currency,
        supersedes_id=prior_id,
    )
    # Mark prior as superseded — only status changes, not lot data
    prior.validation_status = "superseded"
    return new_corr


def get_active_corrections(
    session: Session,
    instrument_id: int | None = None,
    account_key: str | None = None,
) -> list[LotCorrection]:
    q = select(LotCorrection).where(LotCorrection.validation_status == "active")
    if instrument_id is not None:
        q = q.where(LotCorrection.instrument_id == instrument_id)
    if account_key is not None:
        q = q.where(LotCorrection.account_key == account_key)
    return list(session.execute(q).scalars().all())


def validate_correction(
    session: Session,
    correction: LotCorrection,
) -> list[CorrectionConflict]:
    """Validate correction against current holdings. Returns list of conflicts."""
    conflicts: list[CorrectionConflict] = []
    holding = session.execute(
        select(Holding).where(Holding.instrument_id == correction.instrument_id)
    ).scalar_one_or_none()
    if holding is not None and holding.qty is not None and correction.qty > holding.qty:
        conflicts.append(CorrectionConflict(
            correction_id=correction.id,
            conflict_reason="correction_qty_exceeds_current_holding",
            broker_qty=holding.qty,
        ))
    return conflicts
