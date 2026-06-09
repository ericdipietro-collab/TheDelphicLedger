"""Derive TaxLot rows from resolved buy/sell transactions (FIFO) and average-cost fallback.

Invariant: lots are derived data. derive_lots() clears and rebuilds from source records.
Never creates a lot where cost basis is absent (Invariant H).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import Instrument, PositionSnapshot, TaxLot, Transaction

_BUY_TYPES = frozenset({"buy", "reinvest"})
_SELL_TYPES = frozenset({"sell"})
_MIN_QTY = Decimal("0.0001")


def derive_lots(session: Session, fallback_date: date | None = None) -> int:
    """Rebuild all TaxLot rows from transactions and snapshots. Returns lot count written.

    For instruments with resolved buy transactions: FIFO sell-relief → exact lots.
    For instruments with no buy transactions but a cost_basis snapshot: average_fallback lot.
    For instruments with no buy transactions and no cost_basis: no lot (Invariant H).
    """
    session.query(TaxLot).delete()
    session.flush()

    instruments = session.execute(select(Instrument)).scalars().all()
    total = 0

    for inst in instruments:
        iid = inst.id

        buys = session.execute(
            select(Transaction)
            .where(
                Transaction.instrument_id == iid,
                Transaction.canonical_type.in_(_BUY_TYPES),
                Transaction.qty.isnot(None),
                Transaction.price.isnot(None),
                Transaction.account_id.isnot(None),
            )
            .order_by(Transaction.trade_date, Transaction.id)
        ).scalars().all()

        sells = session.execute(
            select(Transaction)
            .where(
                Transaction.instrument_id == iid,
                Transaction.canonical_type.in_(_SELL_TYPES),
                Transaction.qty.isnot(None),
                Transaction.account_id.isnot(None),
            )
            .order_by(Transaction.trade_date, Transaction.id)
        ).scalars().all()

        buy_accts = {t.account_id for t in buys}

        # Accounts with buy transactions: exact lots via FIFO
        for acct in sorted(buy_accts):
            raw_buys: list[tuple[date, Decimal, Decimal]] = []
            for t in buys:
                if t.account_id != acct:
                    continue
                qty = t.qty
                price = t.price
                if not qty or qty <= 0 or not price:
                    continue
                fees = t.fees or Decimal("0")
                cost_per_share = price + (fees / qty)
                raw_buys.append((t.trade_date, qty, cost_per_share))

            raw_sells: list[tuple[date, Decimal]] = [
                (t.trade_date, t.qty)
                for t in sells
                if t.account_id == acct and t.qty
            ]

            for acquired_date, qty, cost_per_share in _fifo_relief(raw_buys, raw_sells):
                session.add(
                    TaxLot(
                        instrument_id=iid,
                        account_id=acct,
                        acquired_date=acquired_date,
                        qty=qty,
                        cost_per_share=cost_per_share,
                        basis_quality="exact",
                    )
                )
                total += 1

        # Accounts without buy transactions: average-cost fallback if snapshot has cost_basis
        fallback_date_used = fallback_date or date.today()
        snap_accts = session.execute(
            select(PositionSnapshot.account_id)
            .where(
                PositionSnapshot.instrument_id == iid,
                PositionSnapshot.qty.isnot(None),
                PositionSnapshot.cost_basis.isnot(None),
                PositionSnapshot.account_id.isnot(None),
            )
            .distinct()
        ).scalars().all()

        for acct_id in sorted(snap_accts):
            if acct_id in buy_accts:
                continue  # already handled with exact lots

            snap = session.execute(
                select(PositionSnapshot)
                .where(
                    PositionSnapshot.instrument_id == iid,
                    PositionSnapshot.account_id == acct_id,
                    PositionSnapshot.qty.isnot(None),
                    PositionSnapshot.cost_basis.isnot(None),
                )
                .order_by(PositionSnapshot.as_of.desc().nullslast(), PositionSnapshot.id.desc())
                .limit(1)
            ).scalar_one_or_none()

            if snap is None or snap.qty is None or snap.cost_basis is None:
                continue
            if snap.qty <= 0:
                continue

            cost_per_share = snap.cost_basis / snap.qty
            acquired_date = snap.as_of or fallback_date_used
            session.add(
                TaxLot(
                    instrument_id=iid,
                    account_id=acct_id,
                    acquired_date=acquired_date,
                    qty=snap.qty,
                    cost_per_share=cost_per_share,
                    basis_quality="average_fallback",
                )
            )
            total += 1

    # Apply user corrections: user wins over broker-derived (FR-4.7)
    from committee.lots.corrections import get_active_corrections
    from committee.models import TaxLot as _TaxLot

    active_corrs = get_active_corrections(session)
    for corr in active_corrs:
        existing = session.execute(
            select(_TaxLot).where(
                _TaxLot.instrument_id == corr.instrument_id,
                _TaxLot.account_id == corr.account_key,
                _TaxLot.acquired_date == corr.acquired_date,
            )
        ).scalar_one_or_none()
        if existing is not None:
            # User correction wins: overwrite cost/qty on derived row
            existing.cost_per_share = corr.cost_per_share
            existing.qty = corr.qty
            existing.basis_quality = corr.basis_quality
            existing.origin = "user_corrected"
        else:
            # No existing derived lot: assert correction as new lot
            session.add(_TaxLot(
                instrument_id=corr.instrument_id,
                account_id=corr.account_key,
                acquired_date=corr.acquired_date,
                qty=corr.qty,
                cost_per_share=corr.cost_per_share,
                basis_quality=corr.basis_quality,
                origin="user_asserted",
            ))
            total += 1

    return total


def _fifo_relief(
    buys: list[tuple[date, Decimal, Decimal]],
    sells: list[tuple[date, Decimal]],
) -> list[tuple[date, Decimal, Decimal]]:
    """Apply FIFO sell-relief. Returns open lots as (acquired_date, qty, cost_per_share)."""
    lots: list[list] = [[d, q, c] for d, q, c in buys]
    for _, sell_qty in sells:
        remaining = sell_qty
        for lot in lots:
            if remaining <= Decimal("0"):
                break
            consumed = min(lot[1], remaining)
            lot[1] -= consumed
            remaining -= consumed
    return [(d, q, c) for d, q, c in lots if q > _MIN_QTY]
