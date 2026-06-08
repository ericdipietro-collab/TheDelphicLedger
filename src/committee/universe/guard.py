"""Scrape guard: instrument cap enforcement for the active universe."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import BundleState, Holding, Instrument

_DEFAULT_CAP = 3000
_WARN_FRACTION = 0.80


def active_universe_size(session: Session) -> int:
    """Count unique instruments in the active universe (holdings + enabled bundles)."""
    holding_ids: set[int] = set(
        session.execute(select(Holding.instrument_id).distinct()).scalars().all()
    )

    enabled_ids: set[str] = set(
        session.execute(
            select(BundleState.id).where(BundleState.enabled.is_(True))
        ).scalars().all()
    )

    if not enabled_ids:
        return len(holding_ids)

    all_instruments = session.execute(select(Instrument)).scalars().all()
    bundle_inst_ids: set[int] = {
        inst.id
        for inst in all_instruments
        if any(tag in enabled_ids for tag in (inst.bundle_tags or []))
    }

    return len(holding_ids | bundle_inst_ids)


def check_guard(
    session: Session,
    new_count: int = 0,
    cap: int = _DEFAULT_CAP,
) -> tuple[str, int]:
    """Check if adding new_count instruments would exceed the cap.

    Returns ('ok' | 'warn' | 'refuse', projected_size).
    Refuse message: "disable a bundle" to reduce the universe.
    """
    current = active_universe_size(session)
    projected = current + new_count
    warn_at = int(cap * _WARN_FRACTION)
    if projected > cap:
        return "refuse", projected
    if projected > warn_at:
        return "warn", projected
    return "ok", projected
