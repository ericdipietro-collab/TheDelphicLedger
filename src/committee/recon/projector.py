"""Roll-forward quantity projector.

Computes expected_qty at T1 given a T0 snapshot plus canonical transaction
effects in (T0, T1].  Coverage is determined at the account level: if the
account has *any* transaction in (T0, T1], the projection is covered.
A recorded instrument_event (split) also counts as coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import InstrumentEvent, Transaction

# canonical_type values that add to qty (use abs value of qty column)
_QTY_ADD = frozenset(["buy", "reinvest", "transfer_in"])
# canonical_type values that subtract from qty (use abs value of qty column)
_QTY_SUB = frozenset(["sell", "transfer_out"])


@dataclass
class QtyEffect:
    effect_date: date
    description: str
    delta: Decimal


@dataclass
class ProjectionResult:
    expected_qty: Decimal
    # True when the account had no transactions AND no events in (T0, T1].
    # An unverifiable projection — we can't distinguish a quiet hold from a
    # missing import.
    coverage_gap: bool
    effects: list[QtyEffect] = field(default_factory=list)


def project_qty(
    session: Session,
    instrument_id: int,
    account_id: str,
    t0_date: date,
    t0_qty: Decimal,
    t1_date: date,
) -> ProjectionResult:
    """Project the expected qty at t1_date given t0_qty at t0_date."""

    # Qty-affecting transactions for this (instrument, account) in (T0, T1]
    txns = session.execute(
        select(Transaction)
        .where(
            Transaction.instrument_id == instrument_id,
            Transaction.account_id == account_id,
            Transaction.trade_date > t0_date,
            Transaction.trade_date <= t1_date,
            Transaction.canonical_type.isnot(None),
        )
        .order_by(Transaction.trade_date, Transaction.id)
    ).scalars().all()

    # Instrument events (splits) for this instrument in (T0, T1]
    events = session.execute(
        select(InstrumentEvent)
        .where(
            InstrumentEvent.instrument_id == instrument_id,
            InstrumentEvent.event_date > t0_date,
            InstrumentEvent.event_date <= t1_date,
        )
        .order_by(InstrumentEvent.event_date, InstrumentEvent.id)
    ).scalars().all()

    # Account-level coverage: any transaction for this account in the window
    account_has_txns = bool(
        session.execute(
            select(Transaction.id)
            .where(
                Transaction.account_id == account_id,
                Transaction.trade_date > t0_date,
                Transaction.trade_date <= t1_date,
            )
            .limit(1)
        ).scalar_one_or_none()
    )

    coverage_gap = not account_has_txns and not events

    # Build the qty-affecting effect list from transactions
    txn_effects: dict[date, list[QtyEffect]] = {}
    for txn in txns:
        ctype = txn.canonical_type
        raw_qty = abs(txn.qty) if txn.qty is not None else Decimal("0")
        if ctype in _QTY_ADD:
            eff = QtyEffect(txn.trade_date, f"{ctype} +{raw_qty}", raw_qty)
        elif ctype in _QTY_SUB:
            eff = QtyEffect(txn.trade_date, f"{ctype} -{raw_qty}", -raw_qty)
        else:
            continue
        txn_effects.setdefault(txn.trade_date, []).append(eff)

    split_by_date: dict[date, list[tuple[Decimal, str]]] = {}
    for ev in events:
        split_by_date.setdefault(ev.event_date, []).append((ev.ratio, ev.event_type))

    all_dates = sorted(set(list(txn_effects.keys()) + list(split_by_date.keys())))

    running_qty = t0_qty
    final_effects: list[QtyEffect] = []

    for d in all_dates:
        # Apply transactions before splits on the same date (conservative ordering)
        for eff in txn_effects.get(d, []):
            running_qty += eff.delta
            final_effects.append(eff)
        for ratio, ev_type in split_by_date.get(d, []):
            old_qty = running_qty
            running_qty = running_qty * ratio
            final_effects.append(
                QtyEffect(d, f"{ev_type} ×{ratio}", running_qty - old_qty)
            )

    return ProjectionResult(
        expected_qty=running_qty,
        coverage_gap=coverage_gap,
        effects=final_effects,
    )
