"""Freshness SLAs and staleness checks per data source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session


@dataclass
class FreshnessSLA:
    source: str
    max_age_days: int


SLAS: dict[str, FreshnessSLA] = {
    "tiingo": FreshnessSLA("tiingo", 1),
    "yfinance": FreshnessSLA("yfinance", 1),
    "fred": FreshnessSLA("fred", 7),
    "edgar_xbrl": FreshnessSLA("edgar_xbrl", 30),
    "edgar_submissions": FreshnessSLA("edgar_submissions", 7),
}


def is_fresh(fetched_at: datetime | None, source: str) -> bool:
    if fetched_at is None:
        return False
    sla = SLAS.get(source)
    if sla is None:
        return False
    utc_now = datetime.now(UTC)
    ft = fetched_at if fetched_at.tzinfo else fetched_at.replace(tzinfo=UTC)
    age_days = (utc_now - ft).days
    return age_days <= sla.max_age_days


def latest_fetch(session: Session, source: str, series_id: str) -> datetime | None:
    from committee.models import MarketObservation

    return session.execute(
        select(func.max(MarketObservation.fetched_at)).where(
            MarketObservation.source == source,
            MarketObservation.series_id == series_id,
        )
    ).scalar_one_or_none()


def freshness_status(session: Session, source: str, series_id: str) -> str:
    """Returns 'fresh', 'stale', or 'missing'."""
    ft = latest_fetch(session, source, series_id)
    if ft is None:
        return "missing"
    return "fresh" if is_fresh(ft, source) else "stale"
