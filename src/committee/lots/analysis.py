"""Tax lot analysis: ST/LT classification, gain/loss, wash-sale detection, unwind ordering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import Instrument, MarketObservation, TaxLot, Transaction


def is_long_term(acquired_date: date, as_of: date) -> bool:
    """Long-term iff held MORE than 1 year (strictly greater than 12 months)."""
    return as_of > acquired_date + relativedelta(years=1)


def days_to_long_term(acquired_date: date, as_of: date) -> int | None:
    """Calendar days until long-term status. None if already long-term."""
    if is_long_term(acquired_date, as_of):
        return None
    lt_date = acquired_date + relativedelta(years=1) + timedelta(days=1)
    return (lt_date - as_of).days


def lot_gain(lot: TaxLot, current_price: Decimal) -> Decimal:
    """Unrealized gain/loss: (current_price - cost_per_share) * qty."""
    return (current_price - lot.cost_per_share) * lot.qty


def wash_sale_risk(
    instrument_id: int,
    account_id: str,
    session: Session,
    sale_date: date,
) -> bool:
    """True if a buy for this instrument exists within 30 days before the sale date.

    The forward 30-day window is unknowable (future buys), so this only checks the
    backward window. The caller should warn the user about the forward window separately.
    """
    window_start = sale_date - timedelta(days=30)
    count = session.execute(
        select(Transaction.id)
        .where(
            Transaction.instrument_id == instrument_id,
            Transaction.account_id == account_id,
            Transaction.canonical_type.in_({"buy", "reinvest"}),
            Transaction.trade_date >= window_start,
            Transaction.trade_date <= sale_date,
        )
        .limit(1)
    ).scalar_one_or_none()
    return count is not None


@dataclass
class LotAnalysis:
    lot: TaxLot
    instrument: Instrument
    current_price: Decimal | None
    unrealized_gain: Decimal | None
    long_term: bool
    days_held: int
    days_to_lt: int | None
    wash_sale_flagged: bool
    basis_quality: str  # "exact" | "average_fallback"


def compute_unwind_analysis(
    session: Session,
    today: date,
    st_rate: Decimal,
    lt_rate: Decimal,
    account_filter: str | None = None,
) -> list[LotAnalysis]:
    """Return lot-level analysis for all needs_unwind instruments.

    Ordered: losses first (largest loss first), then LT gains (ascending),
    then ST gains (ascending, with days_to_lt). Within each bucket: largest
    |gain| first, then oldest acquired_date as tiebreaker.
    """
    q = (
        select(TaxLot)
        .join(Instrument, TaxLot.instrument_id == Instrument.id)
        .where(Instrument.needs_unwind.is_(True))
    )
    if account_filter:
        q = q.where(TaxLot.account_id == account_filter)
    lots = session.execute(q).scalars().all()

    # Latest price per instrument
    price_cache: dict[int, Decimal | None] = {}

    results: list[LotAnalysis] = []
    for lot in lots:
        inst = session.get(Instrument, lot.instrument_id)
        if inst is None:
            continue

        iid = lot.instrument_id
        if iid not in price_cache:
            price_cache[iid] = _latest_price(session, iid)

        price = price_cache[iid]
        gain = lot_gain(lot, price) if price is not None else None
        lt = is_long_term(lot.acquired_date, today)
        days_held = (today - lot.acquired_date).days
        dtlt = days_to_long_term(lot.acquired_date, today)

        flagged = False
        if gain is not None and gain < 0:
            flagged = wash_sale_risk(lot.instrument_id, lot.account_id, session, today)

        results.append(
            LotAnalysis(
                lot=lot,
                instrument=inst,
                current_price=price,
                unrealized_gain=gain,
                long_term=lt,
                days_held=days_held,
                days_to_lt=dtlt,
                wash_sale_flagged=flagged,
                basis_quality=lot.basis_quality,
            )
        )

    # Sort: losses first (largest loss = most negative first), LT gains, ST gains
    def _sort_key(a: LotAnalysis) -> tuple[int, Decimal, date]:
        gain = a.unrealized_gain if a.unrealized_gain is not None else Decimal("0")
        if gain < 0:
            bucket = 0
        elif a.long_term:
            bucket = 1
        else:
            bucket = 2
        return (bucket, gain, a.lot.acquired_date)

    return sorted(results, key=_sort_key)


def _latest_price(session: Session, instrument_id: int) -> Decimal | None:
    row = session.execute(
        select(MarketObservation)
        .where(
            MarketObservation.instrument_id == instrument_id,
            MarketObservation.unit == "USD_adj_close",
            MarketObservation.degraded.is_(False),
        )
        .order_by(MarketObservation.observed_date.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row.value if row else None


def estimated_tax(gain: Decimal, long_term: bool, st_rate: Decimal, lt_rate: Decimal) -> Decimal:
    """Estimated tax on a realized gain (negative = tax benefit from loss)."""
    rate = lt_rate if long_term else st_rate
    return gain * rate
