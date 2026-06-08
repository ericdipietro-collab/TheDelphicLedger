"""Scenarios endpoints: pack list and before/after waterfall."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.schemas import (
    OracleVerdictDelta,
    PackSummary,
    ScenarioResult,
    SleeveWaterfall,
)
from committee.models import Decision, Holding, Instrument
from committee.oracles.base import ORACLE_IDS

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])

SessionDep = Annotated[Session, Depends(get_session)]

_SCENARIOS_DIR = Path(__file__).parent.parent.parent.parent.parent / "scenarios"
_SLEEVES = ["equity_us", "equity_intl", "fixed_income", "alternatives", "cash"]


def _load_pack_yaml(pack_id: str) -> dict:
    path = _SCENARIOS_DIR / f"{pack_id}.yaml"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Pack {pack_id!r} not found.")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _list_pack_ids() -> list[str]:
    if not _SCENARIOS_DIR.exists():
        return []
    return [p.stem for p in sorted(_SCENARIOS_DIR.glob("*.yaml"))]


def _current_sleeve_mvs(session: Session) -> dict[str, Decimal]:
    rows = session.execute(
        select(Holding, Instrument)
        .join(Instrument, Holding.instrument_id == Instrument.id)
    ).all()
    sleeve_mv: dict[str, Decimal] = {s: Decimal("0") for s in _SLEEVES}
    for h, inst in rows:
        sleeve = inst.sleeve or "cash"
        mv = h.market_value or Decimal("0")
        if sleeve in sleeve_mv:
            sleeve_mv[sleeve] += mv
        else:
            sleeve_mv.setdefault(sleeve, Decimal("0"))
            sleeve_mv[sleeve] += mv
    return sleeve_mv


@router.get("", response_model=list[PackSummary])
def list_packs(session: SessionDep) -> list[PackSummary]:
    """List all available scenario packs."""
    pack_ids = _list_pack_ids()

    # Check which packs have a run
    run_scenario_ids = set(
        session.execute(
            select(Decision.scenario_id).where(Decision.scenario_id.is_not(None)).distinct()
        ).scalars().all()
    )

    summaries: list[PackSummary] = []
    for pid in pack_ids:
        try:
            data = _load_pack_yaml(pid)
        except Exception:
            continue
        summaries.append(PackSummary(
            pack_id=pid,
            display_name=data.get("display_name", pid),
            pack_type=data.get("type", "historical"),
            has_run=pid in run_scenario_ids,
        ))
    return summaries


@router.get("/{pack_id}", response_model=ScenarioResult)
def get_scenario_result(pack_id: str, session: SessionDep) -> ScenarioResult:
    """Return before/after waterfall and verdict deltas for a scenario pack."""
    pack_data = _load_pack_yaml(pack_id)
    sleeve_shocks_raw: dict[str, float] = pack_data.get("sleeve_shocks", {})
    sleeve_shocks = {k: Decimal(str(v)) for k, v in sleeve_shocks_raw.items()}

    # Current sleeve MVs for the before column
    before_mvs = _current_sleeve_mvs(session)

    # Build waterfall
    waterfall: list[SleeveWaterfall] = []
    for sleeve in _SLEEVES:
        before = before_mvs.get(sleeve, Decimal("0"))
        shock = sleeve_shocks.get(sleeve, Decimal("0"))
        after = before * (Decimal("1") + shock)
        delta = after - before
        waterfall.append(SleeveWaterfall(
            sleeve=sleeve,
            before_mv=str(before),
            shock_pct=float(shock),
            after_mv=str(after),
            delta_mv=str(delta),
        ))

    # Latest scenario run for this pack
    latest_scenario_row = session.execute(
        select(Decision)
        .where(Decision.scenario_id == pack_id)
        .order_by(Decision.run_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_scenario_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No scenario run found for {pack_id!r}. Run `committee scenario {pack_id}` first.",
        )

    scenario_run_id = latest_scenario_row.run_id
    scenario_run_at = latest_scenario_row.run_at

    # All rows for this scenario run
    scenario_rows = session.execute(
        select(Decision).where(Decision.run_id == scenario_run_id)
    ).scalars().all()
    scenario_by_oracle = {r.persona_key: r for r in scenario_rows}

    # Latest baseline run for comparison
    baseline_row_any = session.execute(
        select(Decision)
        .where(Decision.scenario_id.is_(None))
        .order_by(Decision.run_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    baseline_rows: list[Decision] = []
    if baseline_row_any:
        baseline_rows = list(session.execute(
            select(Decision).where(Decision.run_id == baseline_row_any.run_id)
        ).scalars().all())
    baseline_by_oracle = {r.persona_key: r for r in baseline_rows}

    # Build verdict deltas
    verdict_deltas: list[OracleVerdictDelta] = []
    for oid in ORACLE_IDS:
        s_row = scenario_by_oracle.get(oid)
        b_row = baseline_by_oracle.get(oid)

        def _count_scored(row: Decision | None) -> int:
            if row is None:
                return 0
            scores = (row.outputs_json or {}).get("scores", {})
            return sum(1 for v in scores.values() if v.get("score") is not None)

        def _abstained(row: Decision | None) -> bool:
            if row is None:
                return True
            return bool((row.outputs_json or {}).get("abstained", False))

        display_name = s_row.oracle_name if s_row else (b_row.oracle_name if b_row else oid)
        verdict_deltas.append(OracleVerdictDelta(
            oracle_id=oid,
            display_name=display_name,
            scored_count_baseline=_count_scored(b_row),
            scored_count_scenario=_count_scored(s_row),
            abstained_baseline=_abstained(b_row),
            abstained_scenario=_abstained(s_row),
        ))

    return ScenarioResult(
        pack_id=pack_id,
        display_name=pack_data.get("display_name", pack_id),
        pack_type=pack_data.get("type", "historical"),
        run_id=scenario_run_id,
        run_at=scenario_run_at,
        waterfall=waterfall,
        verdict_deltas=verdict_deltas,
        honesty_note=pack_data.get("honesty_note"),
    )
