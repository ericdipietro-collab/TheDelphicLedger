"""Macro regime signal functions — pure float implementations.

Faithful to docs/reference/signals.py but without numpy/pandas dependencies.
All functions return Score in [-1, +1].  Degraded = stale/missing inputs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Score:
    """A single signal's vote in [-1, +1]."""

    name: str
    value: float
    degraded: bool = False
    meta: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", max(-1.0, min(1.0, float(self.value))))


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def yield_curve(slope_10y3m_bps: float, delta_3m_bps: float = 0.0) -> Score:
    """10y-3m slope. Inversion gates risk-on rather than forcing risk-off."""
    level = _clip(slope_10y3m_bps / 150.0)
    trend = _clip(delta_3m_bps / 100.0, -0.5, 0.5)
    return Score(
        "macro.yield_curve",
        0.7 * level + 0.3 * trend,
        meta={"slope_bps": slope_10y3m_bps},
    )


def credit_spreads(hy_oas_pctile_2y: float, mom_change_bps: float) -> Score:
    """HY OAS vs trailing 2y percentile + 1-month rate of change.

    A blowout (>= +100 bps/month) is a veto: returns -1 regardless of level.
    Check meta["veto"] == 1.0 to detect the circuit-breaker signal.
    """
    if mom_change_bps >= 100.0:
        return Score(
            "macro.credit_spreads",
            -1.0,
            meta={"veto": 1.0, "mom_change_bps": mom_change_bps},
        )
    level = -(hy_oas_pctile_2y - 0.5) * 2.0
    roc = -_clip(mom_change_bps / 100.0)
    return Score(
        "macro.credit_spreads",
        0.6 * level + 0.4 * roc,
        meta={"pctile": hy_oas_pctile_2y, "mom_change_bps": mom_change_bps},
    )


def sahm_rule(sahm_value: float) -> Score:
    """Sahm recession indicator: binary-ish drag once it trips (>= 0.50)."""
    if sahm_value >= 0.50:
        return Score("macro.sahm", -1.0, meta={"sahm": sahm_value})
    return Score(
        "macro.sahm",
        -_clip(sahm_value / 0.50, 0.0, 0.6),
        meta={"sahm": sahm_value},
    )


def vix_term_structure(vix3m_over_vix: float, trend_intact: bool) -> Score:
    """VIX3M/VIX ratio. Backwardation = acute fear.

    Fear is contrarian-bullish while trend is intact; confirms bearishness
    when broken.
    """
    if vix3m_over_vix <= 0:
        return Score("sentiment.vix_term", 0.0, degraded=True)
    backwardation = max(0.0, 1.0 - vix3m_over_vix)
    intensity = min(1.0, backwardation / 0.10)
    val = intensity if trend_intact else -intensity
    return Score(
        "sentiment.vix_term",
        val,
        meta={"ratio": vix3m_over_vix, "trend_intact": float(trend_intact)},
    )


def composite(
    scores: list[Score],
    block_weights: Mapping[str, float],
) -> tuple[float, dict[str, object]]:
    """Weighted composite. Degraded signals excluded; weight redistributes within block.

    A fully degraded block contributes 0 and is reported as degraded.
    Same semantics as docs/reference/signals.py composite().
    """
    blocks: dict[str, list[Score]] = {}
    for s in scores:
        block = s.name.split(".", 1)[0]
        blocks.setdefault(block, []).append(s)

    total, detail = 0.0, {}
    for block, weight in block_weights.items():
        members = [s for s in blocks.get(block, []) if not s.degraded]
        if not members:
            detail[block] = {"score": None, "degraded": True}
            continue
        block_score = sum(s.value for s in members) / len(members)
        detail[block] = {
            "score": round(block_score, 4),
            "degraded": False,
            "signals": {s.name: round(s.value, 4) for s in members},
        }
        total += weight * block_score
    return total, detail
