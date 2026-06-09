"""Percentile-based metric scoring: raw values → scores in [-1, +1].

Each metric has a direction (higher_is_better or lower_is_better).
Score = 2*percentile - 1  for higher_is_better (median → 0, top → +1, bottom → -1).
Score = 1 - 2*percentile  for lower_is_better.
"""

from __future__ import annotations

from dataclasses import dataclass

from committee.core.types import NA, MetricValue

# ── Metric definitions ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricDef:
    id: str
    higher_is_better: bool
    description: str


METRICS: dict[str, MetricDef] = {
    "pe_ratio": MetricDef("pe_ratio", False, "Price/Earnings"),
    "pb_ratio": MetricDef("pb_ratio", False, "Price/Book"),
    "fcf_yield": MetricDef("fcf_yield", True, "FCF Yield"),
    "debt_equity": MetricDef("debt_equity", False, "Debt/Equity"),
    "revenue_yoy": MetricDef("revenue_yoy", True, "Revenue YoY growth"),
    "gross_margin_yoy": MetricDef("gross_margin_yoy", True, "Gross margin trend"),
    "momentum_6m": MetricDef("momentum_6m", True, "6-month price momentum"),
    "dividend_yield": MetricDef("dividend_yield", True, "Trailing dividend yield"),
    "payout_ratio": MetricDef("payout_ratio", False, "Payout ratio"),
    "dividend_growth_years": MetricDef("dividend_growth_years", True, "Consecutive div-increase years"),
    "fund_distribution_yield": MetricDef("fund_distribution_yield", True, "Fund distribution yield"),
    "rsi14": MetricDef("rsi14", False, "RSI(14) — overbought penalty"),
    "ma_cross": MetricDef("ma_cross", True, "50/200 MA cross signal"),
    "momentum_12_1": MetricDef("momentum_12_1", True, "12-1 momentum"),
    "beta_vs_spy": MetricDef("beta_vs_spy", False, "Beta vs SPY (lower = defensive)"),
    "expense_ratio": MetricDef("expense_ratio", False, "Expense ratio"),
    "concentration_hhi": MetricDef("concentration_hhi", False, "Portfolio HHI contribution"),
    "individual_stock_pct": MetricDef("individual_stock_pct", False, "Individual-stock portfolio weight"),
    "implied_turnover": MetricDef("implied_turnover", False, "Implied portfolio turnover"),
    # Quality factor metrics (Invariant E: float ratios, not dollar amounts)
    "roic": MetricDef("roic", True, "Return on Invested Capital (op. income / invested capital)"),
    "gross_profitability": MetricDef("gross_profitability", True, "Gross Profit / Total Assets (Novy-Marx)"),
    "accruals_ratio": MetricDef("accruals_ratio", False, "Accruals ratio — lower = cash-backed earnings"),
}

# Equity-fundamental metrics: n/a for ETFs and mutual funds.
EQUITY_FUNDAMENTAL_METRICS: frozenset[str] = frozenset({
    "pe_ratio", "pb_ratio", "fcf_yield", "debt_equity",
    "revenue_yoy", "gross_margin_yoy", "payout_ratio", "dividend_growth_years",
    "roic", "gross_profitability", "accruals_ratio",
})

# Fund-specific metrics: n/a for stocks.
FUND_SPECIFIC_METRICS: frozenset[str] = frozenset({
    "expense_ratio", "fund_distribution_yield",
})

_FUND_TYPES = frozenset({"etf", "mutual_fund"})


def _percentile(raw: float, universe: list[float]) -> float:
    if not universe:
        return 0.5
    below = sum(1 for v in universe if v < raw)
    tied = sum(1 for v in universe if v == raw)
    return (below + 0.5 * tied) / len(universe)


def percentile_score(
    raw_value: float,
    universe_values: list[float],
    higher_is_better: bool,
) -> float:
    """Map raw_value to [-1, +1] via its percentile in universe_values."""
    pctile = _percentile(raw_value, universe_values)
    raw_score = (2.0 * pctile - 1.0) if higher_is_better else (1.0 - 2.0 * pctile)
    return max(-1.0, min(1.0, raw_score))


def score_holding(
    instrument_id: int,
    instrument_type: str | None,
    raw_metrics: dict[str, float | None],  # metric → raw value | None (missing)
    universe_raw: dict[str, list[float]],  # metric → all universe raw values
    metric_weights: dict[str, float],      # from oracle config
    abstain_floor: float,                  # min fraction of weight that must apply
) -> tuple[float | None, dict[str, MetricValue], list[str]]:
    """Score a holding under one oracle's metric config.

    Returns (score, metric_scores_dict, reason_strings).
    score=None means this oracle abstains for this holding.
    """
    is_fund = instrument_type in _FUND_TYPES
    is_stock = instrument_type == "stock"

    metric_scores: dict[str, MetricValue] = {}
    applicable_weight = 0.0
    total_weight = sum(metric_weights.values())
    weighted_sum = 0.0
    reasons: list[str] = []

    for metric_id, weight in metric_weights.items():
        mdef = METRICS.get(metric_id)
        if mdef is None:
            metric_scores[metric_id] = NA
            continue

        # Fund-aware applicability
        if metric_id in EQUITY_FUNDAMENTAL_METRICS and is_fund:
            metric_scores[metric_id] = NA
            continue
        if metric_id in FUND_SPECIFIC_METRICS and is_stock:
            metric_scores[metric_id] = NA
            continue

        raw = raw_metrics.get(metric_id)
        if raw is None:
            metric_scores[metric_id] = NA
            continue

        universe = universe_raw.get(metric_id, [])
        score = percentile_score(raw, universe, mdef.higher_is_better)
        metric_scores[metric_id] = score
        applicable_weight += weight
        weighted_sum += weight * score
        pctile = _percentile(raw, universe)
        reasons.append(f"{metric_id}={raw:.3g} (p{int(pctile * 100)}, s={score:+.2f})")

    if total_weight == 0 or applicable_weight == 0:
        return None, metric_scores, []

    if applicable_weight / total_weight < abstain_floor:
        return None, metric_scores, []

    final_score = max(-1.0, min(1.0, weighted_sum / applicable_weight))
    return final_score, metric_scores, reasons
