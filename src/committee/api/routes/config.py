"""Config endpoint: engine parameters, regime override, and bundle management."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.api.routes.data import _BUNDLES, BUNDLE_IDS, resync_universe
from committee.api.schemas import ConfigResponse
from committee.models import BundleState, RegimeState
from committee.rebalancer.engine import _DRIFT_ABS, _DRIFT_REL, _MIN_TRADE_USD
from committee.rebalancer.profiles import PROFILE_IDS

router = APIRouter(prefix="/api/config", tags=["config"])
SessionDep = Annotated[Session, Depends(get_session)]

_VALID_TILTS = frozenset(["neutral", "aggressive", "defensive"])


@router.get("", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    """Return current engine parameter defaults and available constraint profiles."""
    return ConfigResponse(
        drift_abs=float(_DRIFT_ABS),
        drift_rel=float(_DRIFT_REL),
        min_trade_usd=float(_MIN_TRADE_USD),
        new_money=0.0,
        available_profiles=PROFILE_IDS,
    )


class RegimeOverride(BaseModel):
    tilt: str  # "neutral" | "aggressive" | "defensive"


class RegimeResponse(BaseModel):
    tilt: str
    composite_score: float | None
    confirmation_count: int


@router.get("/regime", response_model=RegimeResponse)
def get_regime(session: SessionDep) -> RegimeResponse:
    """Return current macro regime state."""
    row = session.get(RegimeState, 1)
    if row is None:
        return RegimeResponse(tilt="neutral", composite_score=None, confirmation_count=0)
    return RegimeResponse(
        tilt=row.tilt,
        composite_score=float(row.composite_score) if row.composite_score is not None else None,
        confirmation_count=row.confirmation_count,
    )


@router.put("/regime", response_model=RegimeResponse)
def set_regime(body: RegimeOverride, session: SessionDep) -> RegimeResponse:
    """Manually override the macro regime tilt. Takes effect on the next Re-convene."""
    if body.tilt not in _VALID_TILTS:
        raise HTTPException(status_code=422, detail=f"tilt must be one of {sorted(_VALID_TILTS)}")
    row = session.get(RegimeState, 1)
    if row is None:
        row = RegimeState(id=1, tilt=body.tilt, confirmation_count=0)
        session.add(row)
    else:
        row.tilt = body.tilt
        row.pending_tilt = None
        row.confirmation_count = 0
    session.commit()
    return RegimeResponse(
        tilt=row.tilt,
        composite_score=float(row.composite_score) if row.composite_score is not None else None,
        confirmation_count=row.confirmation_count,
    )


# ── Bundles ───────────────────────────────────────────────────────────────────

class BundleInfo(BaseModel):
    id: str
    display_name: str
    enabled: bool
    instrument_count: int
    last_refreshed_at: datetime | None


class BundleToggle(BaseModel):
    enabled: bool


@router.get("/bundles", response_model=list[BundleInfo])
def get_bundles(session: SessionDep) -> list[BundleInfo]:
    """Return state of all available bundles."""
    results: list[BundleInfo] = []
    for bundle_id, bundle in _BUNDLES.items():
        state = session.get(BundleState, bundle_id)
        results.append(BundleInfo(
            id=bundle_id,
            display_name=bundle["display_name"],
            enabled=state.enabled if state else False,
            instrument_count=state.instrument_count if state else len(bundle["instruments"]),
            last_refreshed_at=state.last_refreshed_at if state else None,
        ))
    return results


@router.put("/bundles/{bundle_id}", response_model=BundleInfo)
def set_bundle_enabled(bundle_id: str, body: BundleToggle, session: SessionDep) -> BundleInfo:
    """Enable or disable a bundle and resync the buy universe."""
    if bundle_id not in BUNDLE_IDS:
        raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")

    state = session.get(BundleState, bundle_id)
    if state is None:
        raise HTTPException(
            status_code=409,
            detail=f"Bundle '{bundle_id}' not seeded yet — run POST /api/data/seed-bundles first",
        )

    state.enabled = body.enabled
    state.last_refreshed_at = datetime.utcnow()
    resync_universe(session)
    session.commit()

    bundle = _BUNDLES[bundle_id]
    return BundleInfo(
        id=bundle_id,
        display_name=bundle["display_name"],
        enabled=state.enabled,
        instrument_count=state.instrument_count,
        last_refreshed_at=state.last_refreshed_at,
    )
