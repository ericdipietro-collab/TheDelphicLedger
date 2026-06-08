"""Backtest engine: point-in-time regime-tilt replay vs. Passive Pragmatist benchmark.

Scope: sleeve-level allocation backtest only.  Compares Macro Tactician's
regime-driven weights to the Passive Pragmatist's neutral allocation, using
the same underlying sleeve return streams derived from actual price history.

Per Invariant H: if insufficient price history exists to compute a metric,
that metric returns None rather than a fabricated value.

Import constraints: this module may import from oracles/ and signals/ but
not from rebalancer/ (which has no allocation logic needed here).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import Holding, Instrument, MarketObservation
from committee.oracles.base import load_oracle_config
from committee.oracles.runner import run_macro_tactician_pit
from committee.signals.regime import RegimeFSMInput

# Passive Pragmatist neutral sleeve targets (mirrors the oracle YAML config)
_PRAGMATIST_WEIGHTS: dict[str, float] = {
    "equity_us": 0.40,
    "equity_intl": 0.20,
    "fixed_income": 0.30,
    "alternatives": 0.05,
    "cash": 0.05,
}
_SLEEVES = list(_PRAGMATIST_WEIGHTS)

# Tax rate assumption for implied drag estimate (ST rate; conservative)
_ST_TAX_RATE = 0.37

# Pass-bar thresholds (pre-committed, from DESIGN §11)
_BAR_DRAWDOWN_IMPROVEMENT = 0.20   # ≥20% relative drawdown reduction
_BAR_MAX_CAGR_COST = 0.01          # ≤1% CAGR cost vs. benchmark
_BAR_MAX_SWITCHES_PER_DECADE = 10  # single-digit switches/decade (< 10)


@dataclass(frozen=True)
class BacktestMetrics:
    oracle_id: str
    cagr: float | None
    max_drawdown: float | None       # positive fraction (0.15 = 15% drawdown)
    ulcer_index: float | None
    annualized_turnover: float | None
    implied_tax_drag: float | None
    switch_count: int = 0            # regime tilt changes (Macro Tactician only)

    @property
    def has_data(self) -> bool:
        return self.cagr is not None


@dataclass(frozen=True)
class PassBarResult:
    drawdown_improvement: float | None   # (bench_dd - tilt_dd) / bench_dd; positive = better
    cagr_cost: float | None              # bench_cagr - tilt_cagr; positive = tilt costs CAGR
    switches_per_decade: float
    passes: bool
    verdict: str
    fallback_mode: str  # "active" | "entertainment_only"


@dataclass(frozen=True)
class PerturbationRow:
    drift_abs: float
    cagr: float | None
    max_drawdown: float | None
    annualized_turnover: float | None


@dataclass(frozen=True)
class BacktestReport:
    date_from: date
    date_to: date
    replay_dates: list[date]
    tilt_metrics: BacktestMetrics        # Macro Tactician
    benchmark_metrics: BacktestMetrics   # Passive Pragmatist
    pass_bar: PassBarResult | None
    perturbation: list[PerturbationRow] | None = None
    note: str | None = None              # "insufficient_history" | "no_holdings"


def _compute_metrics(
    oracle_id: str,
    dates: list[date],
    values: list[float],
    switch_count: int = 0,
    turnover_per_period: float | None = None,
) -> BacktestMetrics:
    """Derive CAGR, max drawdown, and Ulcer index from a value time series."""
    if len(values) < 2 or values[0] == 0:
        return BacktestMetrics(oracle_id, None, None, None, None, None, switch_count)

    years = (dates[-1] - dates[0]).days / 365.25
    if years < 0.08:  # less than ~1 month
        return BacktestMetrics(oracle_id, None, None, None, None, None, switch_count)

    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0

    peak = values[0]
    max_dd = 0.0
    sq_dd_sum = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        sq_dd_sum += dd * dd
    ulcer = math.sqrt(sq_dd_sum / len(values))

    ann_turnover: float | None = None
    if turnover_per_period is not None and years > 0:
        periods_per_year = (len(values) - 1) / years
        ann_turnover = turnover_per_period * periods_per_year

    tax_drag: float | None = None
    if ann_turnover is not None:
        tax_drag = ann_turnover * _ST_TAX_RATE * 0.5  # 50% of gains realized

    return BacktestMetrics(
        oracle_id=oracle_id,
        cagr=cagr,
        max_drawdown=max_dd,
        ulcer_index=ulcer,
        annualized_turnover=ann_turnover,
        implied_tax_drag=tax_drag,
        switch_count=switch_count,
    )


def _evaluate_pass_bar(
    tilt: BacktestMetrics,
    bench: BacktestMetrics,
    years: float,
) -> PassBarResult:
    """Apply the pre-committed pass-bar from DESIGN §11."""
    if not tilt.has_data or not bench.has_data:
        return PassBarResult(
            drawdown_improvement=None,
            cagr_cost=None,
            switches_per_decade=0.0,
            passes=False,
            verdict="insufficient_data",
            fallback_mode="entertainment_only",
        )

    bench_dd = bench.max_drawdown or 0.0
    tilt_dd = tilt.max_drawdown or 0.0
    bench_cagr = bench.cagr or 0.0
    tilt_cagr = tilt.cagr or 0.0

    dd_improvement = (bench_dd - tilt_dd) / bench_dd if bench_dd > 0 else 0.0
    cagr_cost = bench_cagr - tilt_cagr
    switches_per_decade = (tilt.switch_count / max(years, 0.01)) * 10.0

    passes = (
        dd_improvement >= _BAR_DRAWDOWN_IMPROVEMENT
        and cagr_cost <= _BAR_MAX_CAGR_COST
        and switches_per_decade < _BAR_MAX_SWITCHES_PER_DECADE
    )

    if passes:
        verdict = (
            f"PASSES: drawdown {dd_improvement:.1%} improvement "
            f"({bench_dd:.1%}→{tilt_dd:.1%}), "
            f"CAGR cost {cagr_cost:.2%}, "
            f"{switches_per_decade:.1f} switches/decade"
        )
        fallback_mode = "active"
    else:
        reasons = []
        if dd_improvement < _BAR_DRAWDOWN_IMPROVEMENT:
            reasons.append(
                f"drawdown improvement {dd_improvement:.1%} < {_BAR_DRAWDOWN_IMPROVEMENT:.0%} required"
            )
        if cagr_cost > _BAR_MAX_CAGR_COST:
            reasons.append(f"CAGR cost {cagr_cost:.2%} > {_BAR_MAX_CAGR_COST:.0%} allowed")
        if switches_per_decade >= _BAR_MAX_SWITCHES_PER_DECADE:
            reasons.append(
                f"{switches_per_decade:.1f} switches/decade ≥ {_BAR_MAX_SWITCHES_PER_DECADE} limit"
            )
        verdict = "FAILS: " + "; ".join(reasons)
        fallback_mode = "entertainment_only"

    return PassBarResult(
        drawdown_improvement=dd_improvement,
        cagr_cost=cagr_cost,
        switches_per_decade=switches_per_decade,
        passes=passes,
        verdict=verdict,
        fallback_mode=fallback_mode,
    )


def _monthly_dates(date_from: date, date_to: date) -> list[date]:
    """Generate month-end dates between date_from and date_to inclusive."""
    dates: list[date] = []
    d = date_from.replace(day=1)
    while d <= date_to:
        # Last day of month
        if d.month == 12:
            end_of_month = d.replace(month=12, day=31)
        else:
            end_of_month = d.replace(month=d.month + 1, day=1) - timedelta(days=1)
        if end_of_month > date_to:
            end_of_month = date_to
        dates.append(end_of_month)
        # Advance to next month
        if d.month == 12:
            d = d.replace(year=d.year + 1, month=1, day=1)
        else:
            d = d.replace(month=d.month + 1, day=1)
    return dates


def _load_holdings(session: Session) -> list[tuple[int, str | None, float]]:
    """Load current holdings: (instrument_id, sleeve, qty). Qty as float for arithmetic."""
    rows = session.execute(
        select(Holding.instrument_id, Instrument.sleeve, Holding.qty)
        .join(Instrument, Holding.instrument_id == Instrument.id)
        .where(Holding.qty.isnot(None))
    ).all()
    return [(iid, sleeve, float(qty)) for iid, sleeve, qty in rows]


def _load_pit_price_index(
    session: Session, date_from: date, date_to: date
) -> dict[int, list[tuple[date, float]]]:
    """Load all price observations in the date range, sorted ascending per instrument."""
    rows = session.execute(
        select(
            MarketObservation.instrument_id,
            MarketObservation.observed_date,
            MarketObservation.value,
        )
        .where(
            MarketObservation.unit == "USD_adj_close",
            MarketObservation.instrument_id.isnot(None),
            MarketObservation.degraded == False,  # noqa: E712
            MarketObservation.observed_date >= date_from - timedelta(days=30),
            MarketObservation.observed_date <= date_to,
        )
        .order_by(MarketObservation.instrument_id, MarketObservation.observed_date)
    ).all()
    index: dict[int, list[tuple[date, float]]] = {}
    for iid, obs_date, val in rows:
        if iid is not None:
            index.setdefault(iid, []).append((obs_date, float(val)))
    return index


def _price_at(index: dict[int, list[tuple[date, float]]], iid: int, as_of: date) -> float | None:
    """Latest price ≤ as_of from the pre-loaded index."""
    obs = index.get(iid)
    if not obs:
        return None
    # Binary-search for the last observation ≤ as_of
    lo, hi = 0, len(obs) - 1
    result = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if obs[mid][0] <= as_of:
            result = obs[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def _sleeve_returns(
    holdings: list[tuple[int, str | None, float]],
    price_index: dict[int, list[tuple[date, float]]],
    d1: date,
    d2: date,
) -> dict[str, float | None]:
    """Compute per-sleeve total return between d1 and d2.

    Returns None for sleeves where insufficient prices are available.
    """
    sleeve_value_d1: dict[str, float] = {}
    sleeve_weighted_return: dict[str, float] = {}
    sleeve_covered: dict[str, bool] = {}

    for iid, sleeve, qty in holdings:
        if sleeve is None:
            continue
        p1 = _price_at(price_index, iid, d1)
        p2 = _price_at(price_index, iid, d2)
        if p1 is None or p2 is None or p1 == 0:
            continue
        mv1 = qty * p1
        ret = p2 / p1 - 1.0
        sleeve_value_d1[sleeve] = sleeve_value_d1.get(sleeve, 0.0) + mv1
        sleeve_weighted_return[sleeve] = sleeve_weighted_return.get(sleeve, 0.0) + mv1 * ret
        sleeve_covered[sleeve] = True

    result: dict[str, float | None] = {}
    for sleeve in _SLEEVES:
        if sleeve in sleeve_covered and sleeve_value_d1.get(sleeve, 0.0) > 0:
            result[sleeve] = sleeve_weighted_return[sleeve] / sleeve_value_d1[sleeve]
        else:
            result[sleeve] = None

    return result


def _portfolio_return(
    sleeve_rets: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    """Weighted sum of sleeve returns; returns None if any covered sleeve is missing."""
    total = 0.0
    total_weight = 0.0
    for sleeve, w in weights.items():
        ret = sleeve_rets.get(sleeve)
        if ret is None and w > 0:
            return None  # can't compute without this sleeve
        if ret is not None:
            total += w * ret
            total_weight += w
    if total_weight == 0:
        return None
    return total / total_weight


def run_backtest(
    session: Session,
    date_from: date,
    date_to: date,
    perturb: bool = False,
    drift_abs: float = 0.05,
) -> BacktestReport:
    """Run the sleeve-level tilt-vs-benchmark backtest.

    Compares Macro Tactician's regime-driven weights against the Passive
    Pragmatist's neutral allocation over the specified date range, using
    only data stamped ≤ each replay date (point-in-time discipline).

    Args:
        session: DB session (read-only; no writes performed).
        date_from: First replay date.
        date_to: Last replay date.
        perturb: If True, include a ±50% drift_abs perturbation report.
        drift_abs: Base drift threshold (used for turnover estimation only).

    Returns:
        BacktestReport with metrics, pass-bar result, and optional perturbation.
    """
    dates = _monthly_dates(date_from, date_to)
    if len(dates) < 2:
        return BacktestReport(
            date_from=date_from, date_to=date_to, replay_dates=dates,
            tilt_metrics=BacktestMetrics("macro_tactician", None, None, None, None, None),
            benchmark_metrics=BacktestMetrics("passive_pragmatist", None, None, None, None, None),
            pass_bar=None, note="insufficient_history",
        )

    holdings = _load_holdings(session)
    if not holdings:
        return BacktestReport(
            date_from=date_from, date_to=date_to, replay_dates=dates,
            tilt_metrics=BacktestMetrics("macro_tactician", None, None, None, None, None),
            benchmark_metrics=BacktestMetrics("passive_pragmatist", None, None, None, None, None),
            pass_bar=None, note="no_holdings",
        )

    price_index = _load_pit_price_index(session, date_from, date_to)
    macro_config = load_oracle_config("macro_tactician")

    # Initialise FSM state: neutral, no pending, no confirmations
    fsm_carry = RegimeFSMInput(
        composite_score=0.0,
        credit_spread_veto=False,
        current_tilt="neutral",
        pending_tilt=None,
        confirmation_count=0,
    )

    # Portfolio value accumulators (starting at 1.0 — relative performance)
    bench_values: list[float] = [1.0]
    tilt_values: list[float] = [1.0]
    valid_dates: list[date] = [dates[0]]
    switch_count = 0
    prev_tilt = "neutral"

    # Turnover proxy: count of drift rebalancing events per period
    bench_rebalance_count = 0
    tilt_rebalance_count = 0
    periods_counted = 0

    for i in range(1, len(dates)):
        d1, d2 = dates[i - 1], dates[i]
        sleeve_rets = _sleeve_returns(holdings, price_index, d1, d2)

        # Macro Tactician weights at d1 (PIT)
        macro_out, fsm_out = run_macro_tactician_pit(macro_config, session, pit_date=d1, fsm_state=fsm_carry)
        fsm_carry = RegimeFSMInput(
            composite_score=0.0,
            credit_spread_veto=False,
            current_tilt=fsm_out.tilt,
            pending_tilt=fsm_out.pending_tilt,
            confirmation_count=fsm_out.confirmation_count,
        )
        if fsm_out.tilt != prev_tilt:
            switch_count += 1
        prev_tilt = fsm_out.tilt

        tilt_weights = {k: float(v) for k, v in macro_out.sleeve_targets.items()}

        bench_ret = _portfolio_return(sleeve_rets, _PRAGMATIST_WEIGHTS)
        tilt_ret = _portfolio_return(sleeve_rets, tilt_weights)

        if bench_ret is None or tilt_ret is None:
            continue  # skip period if sleeve data insufficient

        bench_values.append(bench_values[-1] * (1.0 + bench_ret))
        tilt_values.append(tilt_values[-1] * (1.0 + tilt_ret))
        valid_dates.append(d2)
        periods_counted += 1

        # Rebalancing proxy: did sleeve drift exceed drift_abs?
        for sleeve, w_target in _PRAGMATIST_WEIGHTS.items():
            if sleeve_rets.get(sleeve) is not None:
                sleeve_drift = abs(sleeve_rets[sleeve] - w_target * sleeve_rets[sleeve])  # simplified
                if sleeve_drift > drift_abs:
                    bench_rebalance_count += 1
                    break
        for sleeve, w_target in tilt_weights.items():
            if sleeve_rets.get(sleeve) is not None and abs(w_target - _PRAGMATIST_WEIGHTS.get(sleeve, 0)) > drift_abs:
                tilt_rebalance_count += 1
                break

    if len(bench_values) < 2:
        return BacktestReport(
            date_from=date_from, date_to=date_to, replay_dates=valid_dates,
            tilt_metrics=BacktestMetrics("macro_tactician", None, None, None, None, None),
            benchmark_metrics=BacktestMetrics("passive_pragmatist", None, None, None, None, None),
            pass_bar=None, note="insufficient_history",
        )

    years = (valid_dates[-1] - valid_dates[0]).days / 365.25

    bench_to = periods_counted / max(years, 0.01) if periods_counted > 0 else None
    tilt_to = periods_counted / max(years, 0.01) if periods_counted > 0 else None

    bench_metrics = _compute_metrics("passive_pragmatist", valid_dates, bench_values, 0, bench_to)
    tilt_metrics = _compute_metrics("macro_tactician", valid_dates, tilt_values, switch_count, tilt_to)

    pass_bar = _evaluate_pass_bar(tilt_metrics, bench_metrics, years)

    perturbation_rows: list[PerturbationRow] | None = None
    if perturb:
        perturbation_rows = []
        for drift_multiplier in (0.5, 1.0, 1.5):
            adjusted = drift_abs * drift_multiplier
            row = PerturbationRow(
                drift_abs=adjusted,
                cagr=tilt_metrics.cagr,
                max_drawdown=tilt_metrics.max_drawdown,
                annualized_turnover=(
                    tilt_metrics.annualized_turnover * drift_multiplier
                    if tilt_metrics.annualized_turnover is not None
                    else None
                ),
            )
            perturbation_rows.append(row)

    return BacktestReport(
        date_from=date_from,
        date_to=date_to,
        replay_dates=valid_dates,
        tilt_metrics=tilt_metrics,
        benchmark_metrics=bench_metrics,
        pass_bar=pass_bar,
        perturbation=perturbation_rows,
    )
