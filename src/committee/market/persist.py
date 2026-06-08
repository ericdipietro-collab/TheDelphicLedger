"""Helpers to write market observations and fundamentals to the database.

All writes are append-only (never UPDATE existing rows).
Deduplication is done by checking existence before inserting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import Fundamental, MarketObservation


def save_price_observations(
    session: Session,
    source: str,
    ticker: str,
    instrument_id: int | None,
    obs_list: list[tuple[date, Decimal, Decimal]],  # (observed_date, adj_close, dividend)
) -> int:
    """Append price observations that don't already exist. Returns count inserted."""
    existing_dates: set[date] = set(
        session.execute(
            select(MarketObservation.observed_date).where(
                MarketObservation.source == source,
                MarketObservation.series_id == ticker,
                MarketObservation.unit == "USD_adj_close",
            )
        ).scalars().all()
    )
    now = datetime.now()
    inserted = 0
    for obs_date, adj_close, dividend in obs_list:
        if obs_date not in existing_dates:
            session.add(
                MarketObservation(
                    source=source,
                    series_id=ticker,
                    instrument_id=instrument_id,
                    observed_date=obs_date,
                    fetched_at=now,
                    value=adj_close,
                    unit="USD_adj_close",
                    degraded=False,
                )
            )
            inserted += 1
        if dividend > 0:
            session.add(
                MarketObservation(
                    source=source,
                    series_id=ticker,
                    instrument_id=instrument_id,
                    observed_date=obs_date,
                    fetched_at=now,
                    value=dividend,
                    unit="USD_dividend",
                    degraded=False,
                )
            )
    return inserted


def save_fred_observations(
    session: Session,
    series_id: str,
    obs_list: list[tuple[date, Decimal, str]],  # (observed_date, value, unit)
    degraded: bool = False,
) -> int:
    """Append FRED observations that don't already exist. Returns count inserted."""
    existing_dates: set[date] = set(
        session.execute(
            select(MarketObservation.observed_date).where(
                MarketObservation.source == "fred",
                MarketObservation.series_id == series_id,
            )
        ).scalars().all()
    )
    now = datetime.now()
    inserted = 0
    for obs_date, value, unit in obs_list:
        if obs_date in existing_dates:
            continue
        session.add(
            MarketObservation(
                source="fred",
                series_id=series_id,
                instrument_id=None,
                observed_date=obs_date,
                fetched_at=now,
                value=value,
                unit=unit,
                degraded=degraded,
            )
        )
        inserted += 1
    return inserted


def save_fundamentals(
    session: Session,
    instrument_id: int,
    obs_list: list[tuple[date, date | None, str, Decimal, str | None]],
    # (period_end, filed_at, metric, value, unit)
) -> int:
    """Append fundamental observations that don't already exist. Returns count inserted."""
    existing: set[tuple[date, str]] = set(
        session.execute(
            select(Fundamental.period_end, Fundamental.metric).where(
                Fundamental.instrument_id == instrument_id,
            )
        ).all()
    )
    now = datetime.now()
    inserted = 0
    for period_end, filed_at, metric, value, unit in obs_list:
        if (period_end, metric) in existing:
            continue
        session.add(
            Fundamental(
                instrument_id=instrument_id,
                period_end=period_end,
                filed_at=filed_at,
                metric=metric,
                value=value,
                unit=unit,
                fetched_at=now,
            )
        )
        inserted += 1
    return inserted
