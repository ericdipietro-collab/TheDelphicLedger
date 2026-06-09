"""Chamber endpoints: oracle cards, dissent matrix, rivals' objections."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.schemas import (
    ChamberResponse,
    DissentCell,
    DissentRow,
    HoldingScoreOut,
    OracleCard,
    RivalObjection,
    RunSummary,
)
from committee.models import Decision, Instrument
from committee.oracles.base import ORACLE_IDS, load_all_oracle_configs

router = APIRouter(prefix="/api/runs", tags=["chamber"])

SessionDep = Annotated[Session, Depends(get_session)]


def _latest_run_id(session: Session, scenario_id: str | None = None) -> str | None:
    stmt = (
        select(Decision.run_id)
        .where(Decision.scenario_id == scenario_id)
        .order_by(Decision.run_at.desc())
        .limit(1)
    )
    return session.execute(stmt).scalar_one_or_none()


def _load_run_rows(session: Session, run_id: str) -> list[Decision]:
    rows = session.execute(
        select(Decision)
        .where(Decision.run_id == run_id)
        .order_by(Decision.id)
    ).scalars().all()
    return list(rows)


def _build_chamber(rows: list[Decision], session: Session) -> ChamberResponse:
    if not rows:
        raise HTTPException(status_code=404, detail="run not found")

    run_id = rows[0].run_id
    run_at = rows[0].run_at
    scenario_id = rows[0].scenario_id

    # Index rows by persona_key
    by_oracle: dict[str, Decision] = {r.persona_key: r for r in rows}

    # Load all oracle configs for rivals
    try:
        all_configs = {c.id: c for c in load_all_oracle_configs()}
    except Exception:
        all_configs = {}

    # Build instrument ticker map from instrument IDs present in any row
    all_iids: set[int] = set()
    for row in rows:
        for iid_str in (row.outputs_json or {}).get("scores", {}):
            all_iids.add(int(iid_str))
        for p in row.proposals_json or []:
            all_iids.add(int(p["instrument_id"]))

    inst_map: dict[int, Instrument] = {}
    if all_iids:
        insts = session.execute(
            select(Instrument).where(Instrument.id.in_(all_iids))
        ).scalars().all()
        inst_map = {i.id: i for i in insts}

    # Build oracle cards
    oracle_cards: list[OracleCard] = []
    for oid in ORACLE_IDS:
        row = by_oracle.get(oid)
        if row is None:
            continue
        out = row.outputs_json or {}
        scores_raw = out.get("scores", {})
        abstained = bool(out.get("abstained", False))

        scored_count = sum(
            1 for v in scores_raw.values() if v.get("score") is not None
        )

        # Top 3 by score
        scored_pairs = [
            (int(iid), float(v["score"]))
            for iid, v in scores_raw.items()
            if v.get("score") is not None
        ]
        scored_pairs.sort(key=lambda x: x[1], reverse=True)
        top_scores = [
            HoldingScoreOut(
                instrument_id=iid,
                ticker=inst_map[iid].ticker if iid in inst_map else None,
                score=sc,
                reasons=(scores_raw.get(str(iid)) or {}).get("reasons", []),
            )
            for iid, sc in scored_pairs[:3]
        ]

        sleeve_targets_raw = out.get("sleeve_targets", {})

        proposal_count = len(row.proposals_json or [])

        oracle_cards.append(OracleCard(
            oracle_id=oid,
            display_name=row.oracle_name,
            scored_count=scored_count,
            abstained=abstained,
            abstain_reason=out.get("abstain_reason"),
            sleeve_targets=sleeve_targets_raw,
            top_scores=top_scores,
            regime_state=row.regime_state,
            proposal_count=proposal_count,
        ))

    # Build dissent matrix: per instrument, per oracle direction
    # Collect all instrument IDs that have any proposal across all oracles
    proposal_iids: set[int] = set()
    for row in rows:
        for p in row.proposals_json or []:
            proposal_iids.add(int(p["instrument_id"]))

    # Also include top-scored instruments from any oracle
    for row in rows:
        for iid_str in (row.outputs_json or {}).get("scores", {}):
            proposal_iids.add(int(iid_str))

    dissent_matrix: list[DissentRow] = []
    for iid in sorted(proposal_iids):
        inst = inst_map.get(iid)
        cells: list[DissentCell] = []
        for oid in ORACLE_IDS:
            row = by_oracle.get(oid)
            if row is None:
                cells.append(DissentCell(oracle_id=oid, direction=None, qty=None, score=None))
                continue

            # Find direction from proposals
            direction = "hold"
            qty_str = "0"
            for p in row.proposals_json or []:
                if int(p["instrument_id"]) == iid:
                    direction = p["direction"]
                    qty_str = p.get("qty", "0")
                    break

            # Score from outputs
            score_val = None
            scores_raw = (row.outputs_json or {}).get("scores", {})
            if str(iid) in scores_raw:
                score_val = scores_raw[str(iid)].get("score")

            cells.append(DissentCell(
                oracle_id=oid,
                direction=direction,
                qty=qty_str,
                score=score_val,
            ))

        dissent_matrix.append(DissentRow(
            instrument_id=iid,
            ticker=inst.ticker if inst else None,
            name=inst.name if inst else None,
            cells=cells,
        ))

    # Build rivals' objections
    rival_objections: list[RivalObjection] = []
    for oid in ORACLE_IDS:
        config = all_configs.get(oid)
        if not config:
            continue
        row_a = by_oracle.get(oid)
        if not row_a:
            continue

        for rival_id in config.rivals:
            row_b = by_oracle.get(rival_id)
            if not row_b:
                continue

            # Collect directions for each instrument
            dirs_a: dict[int, str] = {}
            for p in row_a.proposals_json or []:
                dirs_a[int(p["instrument_id"])] = p["direction"]

            dirs_b: dict[int, str] = {}
            for p in row_b.proposals_json or []:
                dirs_b[int(p["instrument_id"])] = p["direction"]

            for iid in set(dirs_a) & set(dirs_b):
                da, db = dirs_a[iid], dirs_b[iid]
                if (da == "buy" and db == "sell") or (da == "sell" and db == "buy"):
                    inst = inst_map.get(iid)
                    rival_objections.append(RivalObjection(
                        oracle_id=oid,
                        rival_id=rival_id,
                        instrument_id=iid,
                        ticker=inst.ticker if inst else None,
                        oracle_direction=da,
                        rival_direction=db,
                    ))

    return ChamberResponse(
        run_id=run_id,
        run_at=run_at,
        scenario_id=scenario_id,
        oracle_cards=oracle_cards,
        dissent_matrix=dissent_matrix,
        rival_objections=rival_objections,
    )


@router.get("", response_model=list[RunSummary])
def list_runs(session: SessionDep) -> list[RunSummary]:
    """List all committee runs, latest first."""
    # Group by run_id, pick one row per run
    all_rows = session.execute(
        select(Decision)
        .where(Decision.dry_run == False)  # noqa: E712
        .order_by(Decision.run_at.desc())
    ).scalars().all()

    seen: set[str] = set()
    summaries: list[RunSummary] = []
    for row in all_rows:
        if row.run_id in seen:
            continue
        seen.add(row.run_id)
        # Count rows for this run_id
        oracle_count = sum(1 for r in all_rows if r.run_id == row.run_id)
        proposal_count = sum(len(r.proposals_json or []) for r in all_rows if r.run_id == row.run_id)
        summaries.append(RunSummary(
            run_id=row.run_id,
            run_at=row.run_at,
            oracle_count=oracle_count,
            proposal_count=proposal_count,
            scenario_id=row.scenario_id,
            regime_state=row.regime_state,
        ))

    return summaries


@router.get("/latest", response_model=ChamberResponse)
def get_latest_chamber(session: SessionDep, scenario_id: str | None = None) -> ChamberResponse:
    """Return Chamber data for the most recent non-scenario run (or a specific scenario)."""
    run_id = _latest_run_id(session, scenario_id)
    if run_id is None:
        raise HTTPException(status_code=404, detail="No runs found. Run `committee convene` first.")
    rows = _load_run_rows(session, run_id)
    return _build_chamber(rows, session)


@router.get("/{run_id}", response_model=ChamberResponse)
def get_chamber(run_id: str, session: SessionDep) -> ChamberResponse:
    """Return Chamber data for a specific run."""
    rows = _load_run_rows(session, run_id)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return _build_chamber(rows, session)
