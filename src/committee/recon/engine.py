"""Reconciliation engine: orchestrates projector → explainer → materiality → persist.

Usage::

    from committee.recon.engine import run_recon, resolve_break, ReconConfig
    summary = run_recon(session)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from committee.models import PositionSnapshot, ReconBreak
from committee.recon.explainer import suggest_cause
from committee.recon.projector import project_qty

_DEFAULT_MATERIALITY_SHARES = Decimal("0.5")
_DEFAULT_MATERIALITY_PCT = Decimal("0.001")  # 0.1%


@dataclass
class ReconConfig:
    materiality_shares: Decimal = _DEFAULT_MATERIALITY_SHARES
    materiality_pct: Decimal = _DEFAULT_MATERIALITY_PCT


@dataclass
class ReconSummary:
    open_breaks: int = 0       # material, non-coverage-gap breaks
    coverage_gaps: int = 0     # material breaks where coverage_gap=True
    auto_closed: int = 0       # immaterial deltas, written for audit
    checkpoints_clean: int = 0 # exact-match checkpoints (no break written)


def run_recon(session: Session, config: ReconConfig | None = None) -> ReconSummary:
    """Rebuild all open/auto_closed recon breaks. Preserved resolved breaks."""
    if config is None:
        config = ReconConfig()

    # Rebuild semantics: wipe open + auto_closed, preserve resolved
    session.execute(
        delete(ReconBreak).where(ReconBreak.status.in_(["open", "auto_closed"]))
    )

    summary = ReconSummary()

    # All (instrument, account) pairs that have at least one resolvable snapshot
    pairs = session.execute(
        select(
            PositionSnapshot.instrument_id,
            PositionSnapshot.account_id,
        )
        .where(
            PositionSnapshot.instrument_id.isnot(None),
            PositionSnapshot.account_id.isnot(None),
            PositionSnapshot.as_of.isnot(None),
            PositionSnapshot.qty.isnot(None),
        )
        .distinct()
    ).all()

    for instrument_id, account_id in pairs:
        snapshots = session.execute(
            select(PositionSnapshot)
            .where(
                PositionSnapshot.instrument_id == instrument_id,
                PositionSnapshot.account_id == account_id,
                PositionSnapshot.as_of.isnot(None),
                PositionSnapshot.qty.isnot(None),
            )
            .order_by(PositionSnapshot.as_of, PositionSnapshot.id)
        ).scalars().all()

        # Deduplicate on as_of: later id wins (newer import supersedes)
        by_date: dict[date, PositionSnapshot] = {}
        for snap in snapshots:
            if snap.as_of is not None:
                by_date[snap.as_of] = snap

        sorted_snaps = sorted(by_date.values(), key=lambda s: s.as_of)
        if len(sorted_snaps) < 2:
            continue

        for i in range(len(sorted_snaps) - 1):
            t0_snap = sorted_snaps[i]
            t1_snap = sorted_snaps[i + 1]
            t0_date = t0_snap.as_of
            t1_date = t1_snap.as_of
            t0_qty = t0_snap.qty
            actual_qty = t1_snap.qty

            result = project_qty(
                session, instrument_id, account_id, t0_date, t0_qty, t1_date
            )
            expected_qty = result.expected_qty
            delta = actual_qty - expected_qty

            if delta == Decimal("0"):
                summary.checkpoints_clean += 1
                continue

            abs_delta = abs(delta)
            base = (
                expected_qty
                if expected_qty > 0
                else (actual_qty if actual_qty > 0 else Decimal("1"))
            )
            pct_delta = abs_delta / base if base > 0 else abs_delta

            is_immaterial = (
                abs_delta <= config.materiality_shares
                or pct_delta <= config.materiality_pct
            )

            now = datetime.now()

            if is_immaterial:
                brk = ReconBreak(
                    instrument_id=instrument_id,
                    account_id=account_id,
                    as_of=t1_date,
                    expected_qty=expected_qty,
                    actual_qty=actual_qty,
                    delta=delta,
                    status="auto_closed",
                    resolution_note="Immaterial: below tolerance threshold",
                    coverage_gap=result.coverage_gap,
                    suggested_cause="immaterial",
                    opened_at=now,
                )
                session.add(brk)
                summary.auto_closed += 1
                continue

            cause = suggest_cause(
                session=session,
                instrument_id=instrument_id,
                account_id=account_id,
                t0_date=t0_date,
                t1_date=t1_date,
                t0_qty=t0_qty,
                expected_qty=expected_qty,
                actual_qty=actual_qty,
                delta=delta,
                coverage_gap=result.coverage_gap,
            )

            brk = ReconBreak(
                instrument_id=instrument_id,
                account_id=account_id,
                as_of=t1_date,
                expected_qty=expected_qty,
                actual_qty=actual_qty,
                delta=delta,
                status="open",
                coverage_gap=result.coverage_gap,
                suggested_cause=cause,
                opened_at=now,
            )
            session.add(brk)

            if result.coverage_gap:
                summary.coverage_gaps += 1
            else:
                summary.open_breaks += 1

    return summary


def resolve_break(session: Session, break_id: int, note: str) -> None:
    """Mark a break as resolved with a typed reason. Audit write always happens."""
    brk = session.get(ReconBreak, break_id)
    if brk is None:
        raise ValueError(f"ReconBreak {break_id} not found")
    if brk.status == "resolved":
        raise ValueError(f"ReconBreak {break_id} is already resolved")
    brk.status = "resolved"
    brk.resolution_note = note
    brk.resolved_at = datetime.now()
