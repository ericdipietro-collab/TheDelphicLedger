"""Trades endpoints: proposals per oracle, with live rebalancer recompute."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.schemas import OracleProposals, ProposalOut, TradesResponse
from committee.core.types import HoldingScore, OracleOutput, PersonaConstraints
from committee.models import Decision, Instrument
from committee.oracles.base import ORACLE_IDS

router = APIRouter(prefix="/api/trades", tags=["trades"])

SessionDep = Annotated[Session, Depends(get_session)]


def _instrument_map(session: Session, iids: set[int]) -> dict[int, Instrument]:
    if not iids:
        return {}
    insts = session.execute(select(Instrument).where(Instrument.id.in_(iids))).scalars().all()
    return {i.id: i for i in insts}


def _proposals_from_row(row: Decision, inst_map: dict[int, Instrument]) -> list[ProposalOut]:
    out: list[ProposalOut] = []
    for p in row.proposals_json or []:
        iid = int(p["instrument_id"])
        inst = inst_map.get(iid)
        out.append(ProposalOut(
            instrument_id=iid,
            ticker=inst.ticker if inst else None,
            name=inst.name if inst else None,
            account_id=p["account_id"],
            direction=p["direction"],
            qty=p.get("qty", "0"),
            estimated_value=p.get("estimated_value", "0"),
            rationale_tags=p.get("tags", []),
            oracle_score=p.get("oracle_score"),
            tax_note=p.get("tax_note"),
        ))
    return out


@router.get("", response_model=TradesResponse)
def get_trades(
    session: SessionDep,
    run_id: str | None = Query(default=None),
    constraint: str | None = Query(default=None),
) -> TradesResponse:
    """Return persisted proposals for a run (latest non-scenario run by default)."""
    # Find the run
    if run_id is None:
        row_any = session.execute(
            select(Decision)
            .where(Decision.scenario_id.is_(None))
            .order_by(Decision.run_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row_any is None:
            raise HTTPException(status_code=404, detail="No runs found.")
        run_id = row_any.run_id

    all_rows = session.execute(
        select(Decision).where(Decision.run_id == run_id).order_by(Decision.id)
    ).scalars().all()
    if not all_rows:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    # Build instrument map
    all_iids: set[int] = set()
    for row in all_rows:
        for p in row.proposals_json or []:
            all_iids.add(int(p["instrument_id"]))
    inst_map = _instrument_map(session, all_iids)

    # Filter by constraint if provided
    stored_constraint = (all_rows[0].inputs_json or {}).get("constraint")

    oracle_proposals: list[OracleProposals] = []
    for oid in ORACLE_IDS:
        row = next((r for r in all_rows if r.persona_key == oid), None)
        if row is None:
            continue
        proposals = _proposals_from_row(row, inst_map)
        oracle_proposals.append(OracleProposals(
            oracle_id=oid,
            display_name=row.oracle_name,
            proposals=proposals,
        ))

    return TradesResponse(
        run_id=run_id,
        constraint=constraint or stored_constraint,
        oracle_proposals=oracle_proposals,
    )


@router.get("/export")
def export_trades(
    session: SessionDep,
    format: str = Query(default="fidelity", description="Export format: fidelity or schwab"),
    oracle: str = Query(default="value_purist", description="Oracle ID"),
    run_id: str | None = Query(default=None),
) -> Response:
    """Export trade proposals as a broker-compatible CSV file."""
    from decimal import Decimal

    from committee.core.types import TradeProposal
    from committee.models import Account
    from committee.rebalancer.export import export_fidelity_csv, export_schwab_csv

    if format not in ("fidelity", "schwab"):
        raise HTTPException(status_code=400, detail="format must be 'fidelity' or 'schwab'")

    # Find run_id
    if run_id is None:
        row_any = session.execute(
            select(Decision)
            .where(Decision.scenario_id.is_(None))
            .order_by(Decision.run_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row_any is None:
            raise HTTPException(status_code=404, detail="No runs found.")
        run_id = row_any.run_id

    decision_row = session.execute(
        select(Decision)
        .where(Decision.run_id == run_id, Decision.persona_key == oracle)
    ).scalar_one_or_none()

    if decision_row is None:
        raise HTTPException(status_code=404, detail=f"No decision row for oracle={oracle!r} run_id={run_id!r}")

    # Reconstruct proposals
    proposals: list[TradeProposal] = []
    for p in decision_row.proposals_json or []:
        proposals.append(TradeProposal(
            instrument_id=int(p["instrument_id"]),
            account_id=p["account_id"],
            direction=p["direction"],
            qty=Decimal(str(p.get("qty", "0"))),
            estimated_value=Decimal(str(p.get("estimated_value", "0"))),
            rationale_tags=p.get("tags", []),
            oracle_score=p.get("oracle_score"),
            tax_note=p.get("tax_note"),
        ))

    # Build ticker_map
    all_iids = {p.instrument_id for p in proposals}
    inst_map = _instrument_map(session, all_iids)
    ticker_map: dict[int, str] = {
        iid: inst.ticker
        for iid, inst in inst_map.items()
        if inst.ticker
    }

    # Build account_map (account_key → account_key)
    account_map: dict[str, str] = {
        acct.account_key: acct.account_key
        for acct in session.execute(select(Account)).scalars().all()
    }

    if format == "fidelity":
        csv_str, _ = export_fidelity_csv(proposals, ticker_map, oracle, account_map)
    else:
        csv_str, _ = export_schwab_csv(proposals, ticker_map, oracle, account_map)

    filename = f"trades_{oracle}_{format}.csv"
    return Response(
        content=csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class RecomputeRequest(BaseModel):
    run_id: str | None = None
    constraint: str = "unconstrained"
    drift_abs: float = 0.05
    drift_rel: float = 0.25
    min_trade_usd: float = 200.0
    new_money: float = 0.0


@router.post("/recompute", response_model=TradesResponse)
def recompute_trades(body: RecomputeRequest, session: SessionDep) -> TradesResponse:
    """Re-run rebalancer in-memory from stored oracle outputs. No DB writes."""
    from committee.rebalancer.engine import RebalanceParams, propose
    from committee.rebalancer.profiles import load_constraint_profile

    # Find run_id
    run_id = body.run_id
    if run_id is None:
        row_any = session.execute(
            select(Decision)
            .where(Decision.scenario_id.is_(None))
            .order_by(Decision.run_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row_any is None:
            raise HTTPException(status_code=404, detail="No runs found.")
        run_id = row_any.run_id

    all_rows = session.execute(
        select(Decision).where(Decision.run_id == run_id)
    ).scalars().all()
    if not all_rows:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    params = RebalanceParams(
        drift_abs=Decimal(str(body.drift_abs)),
        drift_rel=Decimal(str(body.drift_rel)),
        min_trade_usd=Decimal(str(body.min_trade_usd)),
        new_money=Decimal(str(body.new_money)),
    )

    try:
        profile = load_constraint_profile(body.constraint)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unknown constraint profile: {body.constraint!r}") from exc

    oracle_proposals: list[OracleProposals] = []
    all_iids: set[int] = set()

    for oid in ORACLE_IDS:
        row = next((r for r in all_rows if r.persona_key == oid), None)
        if row is None:
            continue

        # Reconstruct OracleOutput from stored JSON (lossless round-trip)
        out_json = row.outputs_json or {}
        scores_raw = out_json.get("scores", {})
        per_holding_scores = {
            int(iid): HoldingScore(
                instrument_id=int(iid),
                score=v.get("score"),
                reasons=v.get("reasons", []),
                metrics={},
            )
            for iid, v in scores_raw.items()
        }
        sleeve_targets = {
            k: Decimal(str(v))
            for k, v in out_json.get("sleeve_targets", {}).items()
        }
        oracle_out = OracleOutput(
            oracle_id=oid,
            display_name=row.oracle_name,
            per_holding_scores=per_holding_scores,
            sleeve_targets=sleeve_targets,
            persona_constraints=PersonaConstraints(),
            abstained=bool(out_json.get("abstained", False)),
        )

        proposals = propose(oracle_out, session, constraint_profile=profile, params=params)
        for p in proposals:
            all_iids.add(p.instrument_id)

        inst_map = _instrument_map(session, all_iids)
        oracle_proposals.append(OracleProposals(
            oracle_id=oid,
            display_name=row.oracle_name,
            proposals=[
                ProposalOut(
                    instrument_id=p.instrument_id,
                    ticker=inst_map.get(p.instrument_id, None) and inst_map[p.instrument_id].ticker,
                    name=inst_map.get(p.instrument_id, None) and inst_map[p.instrument_id].name,
                    account_id=p.account_id,
                    direction=p.direction,
                    qty=str(p.qty),
                    estimated_value=str(p.estimated_value),
                    rationale_tags=p.rationale_tags,
                    oracle_score=p.oracle_score,
                    tax_note=p.tax_note,
                )
                for p in proposals
            ],
        ))

    return TradesResponse(
        run_id=run_id,
        constraint=body.constraint,
        oracle_proposals=oracle_proposals,
    )
