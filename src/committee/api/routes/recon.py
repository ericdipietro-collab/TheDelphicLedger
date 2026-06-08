"""Recon endpoint: reconciliation breaks and coverage gaps."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.schemas import ReconBreakOut, ReconResponse
from committee.models import Instrument, ReconBreak

router = APIRouter(prefix="/api/recon", tags=["recon"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.get("", response_model=ReconResponse)
def get_recon(session: SessionDep) -> ReconResponse:
    """Return all reconciliation breaks."""
    breaks = session.execute(
        select(ReconBreak, Instrument)
        .outerjoin(Instrument, ReconBreak.instrument_id == Instrument.id)
        .order_by(ReconBreak.opened_at.desc())
    ).all()

    open_count = sum(1 for b, _ in breaks if b.status == "open")
    gap_count = sum(1 for b, _ in breaks if b.coverage_gap)
    resolved_count = sum(1 for b, _ in breaks if b.status != "open")

    break_rows: list[ReconBreakOut] = []
    for b, inst in breaks:
        break_rows.append(ReconBreakOut(
            id=b.id,
            ticker=inst.ticker if inst else None,
            account_id=b.account_id,
            as_of=str(b.as_of),
            expected_qty=str(b.expected_qty) if b.expected_qty is not None else None,
            actual_qty=str(b.actual_qty) if b.actual_qty is not None else None,
            delta=str(b.delta) if b.delta is not None else None,
            status=b.status,
            coverage_gap=b.coverage_gap,
            suggested_cause=b.suggested_cause,
            resolution_note=b.resolution_note,
        ))

    return ReconResponse(
        open_count=open_count,
        coverage_gap_count=gap_count,
        resolved_count=resolved_count,
        breaks=break_rows,
    )
