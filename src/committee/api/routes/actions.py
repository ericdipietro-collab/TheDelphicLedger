"""Action endpoints: convene (run all oracles) and scenario run from the dashboard."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.routes.chamber import _build_chamber, _load_run_rows
from committee.api.routes.scenarios import _SCENARIOS_DIR, get_scenario_result
from committee.api.schemas import ChamberResponse, ScenarioResult
from committee.models import Decision, RegimeState
from committee.oracles.runner import run_all_oracles
from committee.rebalancer.engine import RebalanceParams, propose
from committee.rebalancer.profiles import load_constraint_profile
from committee.scenarios.loader import load_pack

router = APIRouter(prefix="/api/actions", tags=["actions"])
SessionDep = Annotated[Session, Depends(get_session)]


class ConveneRequest(BaseModel):
    constraint: str = "unconstrained"


class ScenarioRunRequest(BaseModel):
    constraint: str = "unconstrained"


def _run_oracles_and_commit(
    session: Session,
    constraint: str,
    scenario_id: str | None,
    scenario_context=None,
) -> str:
    """Core convene logic shared by convene and scenario-run endpoints."""
    try:
        profile = load_constraint_profile(constraint)
    except FileNotFoundError:
        raise HTTPException(status_code=400, detail=f"Constraint profile '{constraint}' not found.")

    run_id = str(uuid.uuid4())
    regime_row = session.get(RegimeState, 1)
    regime_snapshot = regime_row.tilt if regime_row else "neutral"

    oracle_outputs = run_all_oracles(session, scenario=scenario_context)

    for output in oracle_outputs:
        proposals = propose(
            output, session,
            constraint_profile=profile,
            params=RebalanceParams(),
            scenario=scenario_context,
        )
        scored = sum(1 for h in output.per_holding_scores.values() if h.score is not None)
        inputs_snapshot = {
            "holdings_count": scored,
            "constraint": constraint,
            "scenario": scenario_id,
        }
        outputs_snapshot: dict = {
            "scores": {
                str(iid): {"score": hs.score, "reasons": hs.reasons}
                for iid, hs in output.per_holding_scores.items()
            },
            "sleeve_targets": {k: str(v) for k, v in output.sleeve_targets.items()},
            "abstained": output.abstained,
            "abstain_reason": output.abstain_reason,
        }
        proposals_snapshot = [
            {
                "instrument_id": p.instrument_id,
                "account_id": p.account_id,
                "direction": p.direction,
                "qty": str(p.qty),
                "estimated_value": str(p.estimated_value),
                "tags": p.rationale_tags,
                "oracle_score": p.oracle_score,
                "tax_note": p.tax_note,
            }
            for p in proposals
        ]
        session.add(Decision(
            run_id=run_id,
            oracle_name=output.display_name,
            persona_key=output.oracle_id,
            inputs_json=inputs_snapshot,
            outputs_json=outputs_snapshot,
            proposals_json=proposals_snapshot,
            regime_state=regime_snapshot,
            scenario_id=scenario_id,
            dry_run=False,
        ))

    session.commit()
    return run_id


@router.post("/convene", response_model=ChamberResponse)
def api_convene(req: ConveneRequest, session: SessionDep) -> ChamberResponse:
    """Run all six oracles and rebalancer, write audit rows, return the new Chamber run."""
    run_id = _run_oracles_and_commit(
        session=session,
        constraint=req.constraint,
        scenario_id=None,
        scenario_context=None,
    )
    rows = _load_run_rows(session, run_id)
    return _build_chamber(rows, session)


@router.post("/scenario/{pack_id}/run", response_model=ScenarioResult)
def api_run_scenario(pack_id: str, req: ScenarioRunRequest, session: SessionDep) -> ScenarioResult:
    """Run a scenario pack (shocked oracle run), write audit rows, return results."""
    try:
        pack_obj = load_pack(pack_id, _SCENARIOS_DIR)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Scenario pack '{pack_id}' not found.")

    _run_oracles_and_commit(
        session=session,
        constraint=req.constraint,
        scenario_id=pack_id,
        scenario_context=pack_obj.context,
    )
    # Return the full scenario result (uses the same GET logic — now has a run)
    return get_scenario_result(pack_id=pack_id, session=session)
