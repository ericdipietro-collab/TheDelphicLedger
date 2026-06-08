"""Signal engine: every signal emits a Score in [-1, +1] (risk-off ... risk-on).

Design rules (see docs/DESIGN.md):
- Pure functions of input series. No I/O, no LLM, no side effects.
- Each signal returns (score, meta) so decisions are fully auditable.
- Sentiment signals are U-shaped: they only vote at distribution extremes,
  and their sign is conditional on trend (fear in an uptrend is contrarian-
  bullish; fear in a downtrend is confirmation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Score:
    """A single signal's vote."""

    name: str
    value: float  # in [-1, +1]
    degraded: bool = False  # stale/missing inputs -> excluded from composite
    meta: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", float(np.clip(self.value, -1.0, 1.0)))


# --------------------------------------------------------------------------
# Trend block
# --------------------------------------------------------------------------

def sma_distance(prices: pd.Series, window: int = 200, dead_band: float = 0.02,
                 full_scale: float = 0.10) -> Score:
    """Price vs. its own SMA, with a neutral dead-band to suppress whipsaw.

    Score is 0 inside +/-dead_band of the SMA, then scales linearly to +/-1
    at +/-full_scale distance.
    """
    if len(prices) < window:
        return Score("trend.sma_distance", 0.0, degraded=True)
    sma = prices.rolling(window).mean().iloc[-1]
    dist = (prices.iloc[-1] - sma) / sma
    if abs(dist) <= dead_band:
        val = 0.0
    else:
        sign = np.sign(dist)
        val = sign * min(1.0, (abs(dist) - dead_band) / (full_scale - dead_band))
    return Score("trend.sma_distance", val, meta={"distance": round(float(dist), 4)})


def dual_momentum(prices: pd.Series, tbill_annual_rate: float = 0.05) -> Score:
    """Absolute 12-1 momentum vs. T-bills (Antonacci-style).

    Trailing 12-month return excluding the most recent month, compared to the
    risk-free return over the same period. Positive excess -> risk-on.
    """
    # ~252 trading days/yr, ~21/mo. Need 13 months of history.
    if len(prices) < 273:
        return Score("trend.dual_momentum", 0.0, degraded=True)
    p_end = prices.iloc[-21]      # skip most recent month
    p_start = prices.iloc[-273]   # 13 months back
    ret_12_1 = p_end / p_start - 1.0
    rf = tbill_annual_rate * (11 / 12)
    excess = ret_12_1 - rf
    # +/-15% excess saturates the score.
    return Score("trend.dual_momentum", excess / 0.15,
                 meta={"ret_12_1": round(float(ret_12_1), 4)})


def breadth(pct_above_200dma: float) -> Score:
    """% of constituents above their own 200dma, centered at 50%."""
    if not (0.0 <= pct_above_200dma <= 1.0):
        return Score("trend.breadth", 0.0, degraded=True)
    return Score("trend.breadth", (pct_above_200dma - 0.5) / 0.35,
                 meta={"pct": pct_above_200dma})


# --------------------------------------------------------------------------
# Macro block
# --------------------------------------------------------------------------

def yield_curve(slope_10y3m_bps: float, delta_3m_bps: float = 0.0) -> Score:
    """10y-3m slope. Inversion gates risk-on rather than forcing risk-off."""
    level = np.clip(slope_10y3m_bps / 150.0, -1.0, 1.0)
    trend = np.clip(delta_3m_bps / 100.0, -0.5, 0.5)
    return Score("macro.yield_curve", 0.7 * level + 0.3 * trend,
                 meta={"slope_bps": slope_10y3m_bps})


def credit_spreads(hy_oas_pctile_2y: float, mom_change_bps: float) -> Score:
    """HY OAS vs. trailing 2y percentile + 1-month rate of change.

    A blowout (>+100bps/month) is a veto: returns -1 regardless of level.
    """
    if mom_change_bps >= 100.0:
        return Score("macro.credit_spreads", -1.0,
                     meta={"veto": 1.0, "mom_change_bps": mom_change_bps})
    level = -(hy_oas_pctile_2y - 0.5) * 2.0       # high percentile = wide = bad
    roc = -np.clip(mom_change_bps / 100.0, -1.0, 1.0)
    return Score("macro.credit_spreads", 0.6 * level + 0.4 * roc,
                 meta={"pctile": hy_oas_pctile_2y, "mom_change_bps": mom_change_bps})


def sahm_rule(sahm_value: float) -> Score:
    """Sahm recession indicator: binary-ish drag once it trips (>=0.50)."""
    if sahm_value >= 0.50:
        return Score("macro.sahm", -1.0, meta={"sahm": sahm_value})
    return Score("macro.sahm", -np.clip(sahm_value / 0.50, 0.0, 0.6),
                 meta={"sahm": sahm_value})


# --------------------------------------------------------------------------
# Sentiment block -- U-shaped, trend-conditional
# --------------------------------------------------------------------------

def _u_shaped(pctile: float, lo: float = 0.20, hi: float = 0.80) -> float:
    """0 inside [lo, hi]; ramps to 1 at the tails. Returns tail intensity."""
    if pctile < lo:
        return (lo - pctile) / lo          # low-tail intensity, in (0, 1]
    if pctile > hi:
        return (pctile - hi) / (1.0 - hi)  # high-tail intensity
    return 0.0


def vix_term_structure(vix3m_over_vix: float, trend_intact: bool) -> Score:
    """VIX3M/VIX ratio. Backwardation (<1) = acute fear.

    Fear is contrarian-bullish while trend is intact, confirmation when broken.
    """
    if vix3m_over_vix <= 0:
        return Score("sentiment.vix_term", 0.0, degraded=True)
    backwardation = max(0.0, 1.0 - vix3m_over_vix)  # 0 = calm, 0.1 = 10% inverted
    intensity = min(1.0, backwardation / 0.10)
    val = intensity if trend_intact else -intensity
    return Score("sentiment.vix_term", val,
                 meta={"ratio": vix3m_over_vix, "trend_intact": float(trend_intact)})


def aaii_spread(bull_bear_pctile: float, trend_intact: bool = True) -> Score:
    """AAII bull-bear spread percentile, contrarian at extremes only."""
    if not (0.0 <= bull_bear_pctile <= 1.0):
        return Score("sentiment.aaii", 0.0, degraded=True)
    tail = _u_shaped(bull_bear_pctile)
    if tail == 0.0:
        return Score("sentiment.aaii", 0.0, meta={"pctile": bull_bear_pctile})
    if bull_bear_pctile < 0.5:  # extreme bearishness
        val = tail if trend_intact else 0.3 * tail
    else:                        # extreme bullishness -> contrarian negative
        val = -tail
    return Score("sentiment.aaii", val, meta={"pctile": bull_bear_pctile})


# --------------------------------------------------------------------------
# Composite
# --------------------------------------------------------------------------

def composite(scores: list[Score], block_weights: Mapping[str, float]) -> tuple[float, dict]:
    """Weighted composite. Degraded signals are excluded and their weight
    redistributes within the block; a fully degraded block contributes 0 and
    is reported so the decision layer can choose 'no change'.
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
        block_score = float(np.mean([s.value for s in members]))
        detail[block] = {"score": round(block_score, 4), "degraded": False,
                         "signals": {s.name: round(s.value, 4) for s in members}}
        total += weight * block_score
    return total, detail
