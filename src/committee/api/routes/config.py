"""Config endpoint: engine parameters (read-only display; recompute via /trades/recompute)."""

from __future__ import annotations

from fastapi import APIRouter

from committee.api.schemas import ConfigResponse
from committee.rebalancer.engine import _DRIFT_ABS, _DRIFT_REL, _MIN_TRADE_USD
from committee.rebalancer.profiles import PROFILE_IDS

router = APIRouter(prefix="/api/config", tags=["config"])


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
