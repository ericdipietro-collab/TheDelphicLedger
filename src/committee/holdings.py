"""Holdings derivation: re-computed from latest position snapshots on every run.

Holdings are never a source of truth. The derivation joins snapshots against
the instrument security master (by ticker and aliases) without touching the
immutable snapshot rows (Invariant B).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from committee.models import Holding, ImportBatch, Instrument, PositionSnapshot
from committee.resolver.aliases import SWEEP_ALIASES
from committee.resolver.normalize import normalize_ticker


@dataclass
class HoldingRow:
    raw_instrument: str
    instrument_id: int | None
    ticker: str | None
    name: str | None
    instrument_type: str | None
    account_id: str | None
    as_of: date | None
    qty: Decimal | None
    market_value: Decimal | None
    is_resolved: bool


def resolve_local(raw_instrument: str, session: Session) -> Instrument | None:
    """Local-only, fast resolution used by holdings derivation.

    Checks exact ticker, normalized ticker, and aliases only.
    No fuzzy matching, no network calls — unresolved raws surface as None.
    """
    # Step 1: exact ticker
    inst = session.execute(
        select(Instrument).where(Instrument.ticker == raw_instrument)
    ).scalar_one_or_none()
    if inst is not None:
        return inst

    # Check alias table for known sweeps first (avoids full table scan)
    canonical_key = SWEEP_ALIASES.get(raw_instrument.upper())
    if canonical_key is not None:
        inst = session.execute(
            select(Instrument).where(Instrument.ticker == canonical_key)
        ).scalar_one_or_none()
        if inst is not None:
            return inst

    # Step 2: normalized ticker + stored aliases
    norm = normalize_ticker(raw_instrument)
    all_insts = session.execute(select(Instrument)).scalars().all()
    for inst in all_insts:
        if inst.ticker and normalize_ticker(inst.ticker) == norm:
            return inst
        aliases: list[str] = inst.aliases or []
        if raw_instrument in aliases or any(normalize_ticker(a) == norm for a in aliases):
            return inst

    return None


def _get_latest_snapshots(session: Session) -> list[PositionSnapshot]:
    """Return the latest position snapshot per (account_id, raw_instrument).

    "Latest" is defined by the batch's imported_at timestamp.
    Python-side deduplication avoids NULL-equality issues in SQL JOINs.
    """
    stmt = (
        select(PositionSnapshot, ImportBatch.imported_at)
        .join(ImportBatch, PositionSnapshot.batch_id == ImportBatch.id)
        .where(PositionSnapshot.raw_instrument.isnot(None))
        .order_by(ImportBatch.imported_at.desc())
    )
    rows = session.execute(stmt).all()

    seen: dict[tuple[str | None, str | None], bool] = {}
    latest: list[PositionSnapshot] = []
    for snapshot, _ in rows:
        key = (snapshot.account_id, snapshot.raw_instrument)
        if key not in seen:
            seen[key] = True
            latest.append(snapshot)
    return latest


def rebuild_holdings(session: Session) -> list[HoldingRow]:
    """Truncate the holdings table and rebuild from latest position snapshots.

    Returns HoldingRow objects for display, including unresolved instruments
    (which cannot be persisted because instrument_id is NOT NULL on holdings).
    """
    session.execute(delete(Holding))

    snapshots = _get_latest_snapshots(session)
    results: list[HoldingRow] = []

    for snap in snapshots:
        raw = snap.raw_instrument or ""
        inst = resolve_local(raw, session)

        row = HoldingRow(
            raw_instrument=raw,
            instrument_id=inst.id if inst else None,
            ticker=inst.ticker if inst else None,
            name=inst.name if inst else None,
            instrument_type=inst.instrument_type if inst else None,
            account_id=snap.account_id,
            as_of=snap.as_of,
            qty=snap.qty,
            market_value=snap.market_value,
            is_resolved=inst is not None,
        )
        results.append(row)

        if inst is not None and snap.qty is not None:
            session.add(
                Holding(
                    instrument_id=inst.id,
                    account_id=snap.account_id,
                    as_of=snap.as_of or date.today(),
                    qty=snap.qty,
                    market_value=snap.market_value,
                    derived_at=datetime.now(),
                )
            )

    return results


def household_view(rows: list[HoldingRow]) -> list[HoldingRow]:
    """Aggregate holdings across all accounts into a single household row per instrument."""
    grouped: dict[str, HoldingRow] = {}
    for row in rows:
        key = row.ticker or row.raw_instrument
        if key not in grouped:
            grouped[key] = HoldingRow(
                raw_instrument=row.raw_instrument,
                instrument_id=row.instrument_id,
                ticker=row.ticker,
                name=row.name,
                instrument_type=row.instrument_type,
                account_id=None,
                as_of=row.as_of,
                qty=row.qty or Decimal("0"),
                market_value=row.market_value,
                is_resolved=row.is_resolved,
            )
        else:
            agg = grouped[key]
            if row.qty is not None:
                agg.qty = (agg.qty or Decimal("0")) + row.qty
            if row.market_value is not None:
                agg.market_value = (agg.market_value or Decimal("0")) + row.market_value
            if row.as_of and agg.as_of and row.as_of > agg.as_of:
                agg.as_of = row.as_of
    return list(grouped.values())
