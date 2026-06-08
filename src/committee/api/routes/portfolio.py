"""Portfolio endpoint: household allocation, drift gauges, holdings table."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.schemas import (
    DriftGauge,
    HoldingRow,
    PortfolioResponse,
    SleeveAllocation,
)
from committee.models import Decision, Holding, Instrument
from committee.oracles.base import ORACLE_IDS

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

SessionDep = Annotated[Session, Depends(get_session)]

_SLEEVES = ["equity_us", "equity_intl", "fixed_income", "alternatives", "cash"]


@router.get("", response_model=PortfolioResponse)
def get_portfolio(
    session: SessionDep,
    oracle: str = Query(default="value_purist", description="Oracle ID for drift targets"),
) -> PortfolioResponse:
    """Household allocation, drift vs. selected oracle, and holdings table."""
    # Load all current holdings
    holdings = session.execute(
        select(Holding, Instrument)
        .join(Instrument, Holding.instrument_id == Instrument.id)
        .order_by(Instrument.sleeve, Instrument.ticker)
    ).all()

    if not holdings:
        return PortfolioResponse(
            as_of="n/a",
            total_market_value="0",
            allocations=[],
            drift_gauges=[],
            holdings=[],
            selected_oracle=oracle,
        )

    # Compute sleeve MVs (household rollup — Invariant C)
    sleeve_mv: dict[str, Decimal] = {s: Decimal("0") for s in _SLEEVES}
    total_mv = Decimal("0")
    as_of_dates: list[str] = []

    for h, inst in holdings:
        mv = h.market_value or Decimal("0")
        sleeve = inst.sleeve or "cash"
        if sleeve in sleeve_mv:
            sleeve_mv[sleeve] += mv
        else:
            sleeve_mv.setdefault(sleeve, Decimal("0"))
            sleeve_mv[sleeve] += mv
        total_mv += mv
        as_of_dates.append(str(h.as_of))

    as_of = max(as_of_dates) if as_of_dates else "n/a"

    allocations: list[SleeveAllocation] = []
    for sleeve in _SLEEVES:
        mv = sleeve_mv.get(sleeve, Decimal("0"))
        weight = float(mv / total_mv) if total_mv > 0 else 0.0
        allocations.append(SleeveAllocation(sleeve=sleeve, market_value=str(mv), weight=weight))

    # Load latest non-scenario run for the selected oracle's sleeve targets
    oracle_targets: dict[str, Decimal] = {}
    latest_row = session.execute(
        select(Decision)
        .where(Decision.persona_key == oracle, Decision.scenario_id.is_(None))
        .order_by(Decision.run_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_row:
        raw_targets = (latest_row.outputs_json or {}).get("sleeve_targets", {})
        oracle_targets = {k: Decimal(str(v)) for k, v in raw_targets.items()}

    drift_gauges: list[DriftGauge] = []
    for sleeve in _SLEEVES:
        actual = float(sleeve_mv.get(sleeve, Decimal("0")) / total_mv) if total_mv > 0 else 0.0
        target = float(oracle_targets.get(sleeve, Decimal("0")))
        drift = actual - target
        outside = abs(drift) > 0.05  # 5% abs band
        drift_gauges.append(DriftGauge(
            sleeve=sleeve,
            actual_weight=actual,
            target_weight=target,
            drift_abs=drift,
            outside_band=outside,
        ))

    # Load latest oracle scores for all oracles
    oracle_scores_by_iid: dict[int, dict[str, float | None]] = {}
    for oid in ORACLE_IDS:
        row = session.execute(
            select(Decision)
            .where(Decision.persona_key == oid, Decision.scenario_id.is_(None))
            .order_by(Decision.run_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if not row:
            continue
        scores_raw = (row.outputs_json or {}).get("scores", {})
        for iid_str, v in scores_raw.items():
            iid = int(iid_str)
            oracle_scores_by_iid.setdefault(iid, {})
            oracle_scores_by_iid[iid][oid] = v.get("score")

    # Build holdings rows
    seen_holding_keys: set[tuple[int, str | None]] = set()
    holding_rows: list[HoldingRow] = []
    for h, inst in holdings:
        key = (inst.id, h.account_id)
        if key in seen_holding_keys:
            continue
        seen_holding_keys.add(key)
        scores_for_inst = oracle_scores_by_iid.get(inst.id, {})
        holding_rows.append(HoldingRow(
            instrument_id=inst.id,
            ticker=inst.ticker,
            name=inst.name,
            instrument_type=inst.instrument_type,
            sleeve=inst.sleeve,
            account_id=h.account_id,
            qty=str(h.qty),
            market_value=str(h.market_value or Decimal("0")),
            oracle_scores={oid: scores_for_inst.get(oid) for oid in ORACLE_IDS},
            has_8k_flag=False,  # 8-K data not yet wired (Invariant H: no fabricated flags)
        ))

    return PortfolioResponse(
        as_of=as_of,
        total_market_value=str(total_mv),
        allocations=allocations,
        drift_gauges=drift_gauges,
        holdings=holding_rows,
        selected_oracle=oracle,
    )
