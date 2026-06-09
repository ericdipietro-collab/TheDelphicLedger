"""Oracle runner: execute one or all six oracles against current DB state.

Each oracle run:
1. Loads its config (YAML).
2. Fetches universe metrics (metrics.py).
3. Scores each holding in the active universe (scoring.py).
4. For the Macro Tactician: runs the regime FSM to determine sleeve targets.
5. Emits OracleOutput (never TradeProposals - Invariant A).

Import constraint: oracles/ must not import rebalancer/.

run_macro_tactician_pit is a backtest-only variant that accepts an explicit
pit_date and a carry-forward FSM state, never persisting to the DB.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.core.types import (
    HoldingScore,
    OracleOutput,
    PersonaConstraints,
    ScenarioContext,
)
from committee.models import Holding, Instrument, MarketObservation, UniverseEntry
from committee.oracles.base import ORACLE_IDS, OracleConfig, load_oracle_config
from committee.oracles.metrics import compute_universe_metrics
from committee.oracles.scoring import (
    score_holding,
)
from committee.signals.composite import (
    Score,
    composite,
    credit_spreads,
    sahm_rule,
    vix_term_structure,
    yield_curve,
)
from committee.signals.regime import (
    RegimeFSMInput,
    RegimeFSMOutput,
    load_state,
    save_state,
    transition,
)

_FUND_TYPES = frozenset({"etf", "mutual_fund"})


def _build_persona_constraints(raw: dict[str, object]) -> PersonaConstraints:
    return PersonaConstraints(
        min_yield=Decimal(str(raw["min_yield"])) if "min_yield" in raw else None,
        max_positions=int(raw["max_positions"]) if "max_positions" in raw else None,
        individual_stock_target=(
            Decimal(str(raw["individual_stock_target"]))
            if "individual_stock_target" in raw
            else None
        ),
        min_score_to_buy=float(raw["min_score_to_buy"]) if "min_score_to_buy" in raw else None,
    )


def _fetch_active_instruments(session: Session) -> dict[int, Instrument]:
    """All instruments that are held OR are active buy candidates (in UniverseEntry)."""
    held = session.execute(
        select(Instrument)
        .join(Holding, Holding.instrument_id == Instrument.id)
        .distinct()
    ).scalars().all()
    universe = session.execute(
        select(Instrument)
        .join(UniverseEntry, UniverseEntry.instrument_id == Instrument.id)
        .distinct()
    ).scalars().all()
    combined = {inst.id: inst for inst in held}
    combined.update({inst.id: inst for inst in universe})
    return combined


def _run_macro_tactician(
    config: OracleConfig,
    session: Session,
    scenario: ScenarioContext | None = None,
    persist_regime: bool = True,
) -> OracleOutput:
    """Macro Tactician: regime FSM → sleeve targets. No per-holding scores."""
    overrides = scenario.indicator_overrides if scenario else {}

    def _last_value(series_id: str) -> float | None:
        if series_id in overrides:
            return overrides[series_id]
        val = session.execute(
            select(MarketObservation.value)
            .where(
                MarketObservation.series_id == series_id,
                MarketObservation.degraded == False,  # noqa: E712
            )
            .order_by(MarketObservation.observed_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return float(val) if val is not None else None

    def _last_two_values(series_id: str) -> tuple[float | None, float | None]:
        if series_id in overrides:
            # Scenario provides current value; treat previous as neutral (0 MoM spread change)
            cur = overrides[series_id]
            return cur, cur
        rows = session.execute(
            select(MarketObservation.value)
            .where(
                MarketObservation.series_id == series_id,
                MarketObservation.degraded == False,  # noqa: E712
            )
            .order_by(MarketObservation.observed_date.desc())
            .limit(2)
        ).scalars().all()
        if len(rows) >= 2:
            return float(rows[0]), float(rows[1])
        if len(rows) == 1:
            return float(rows[0]), None
        return None, None

    t10y3m = _last_value("T10Y3M")
    hy_oas_cur, hy_oas_prev = _last_two_values("BAMLH0A0HYM2")
    vixcls = _last_value("VIXCLS")
    sahm_val = _last_value("UNRATE")  # Sahm not a standard FRED ticker; degrade gracefully

    scores: list[Score] = []

    # Yield curve (macro block)
    if t10y3m is not None:
        # T10Y3M is in percentage points; convert to bps for the signal
        slope_bps = t10y3m * 100.0
        scores.append(yield_curve(slope_bps))

    # Credit spreads (macro block)
    if hy_oas_cur is not None:
        mom_bps = (
            (hy_oas_cur - hy_oas_prev) * 100.0 if hy_oas_prev is not None else 0.0
        )
        # Compute 2-year percentile from DB observations
        obs_2y = session.execute(
            select(MarketObservation.value)
            .where(MarketObservation.series_id == "BAMLH0A0HYM2")
            .order_by(MarketObservation.observed_date.desc())
            .limit(504)  # ~2 years of daily data
        ).scalars().all()
        if obs_2y:
            vals_2y = sorted(float(v) for v in obs_2y)
            n2 = len(vals_2y)
            below = sum(1 for v in vals_2y if v < hy_oas_cur)
            pctile_2y = (below + 0.5) / n2
        else:
            pctile_2y = 0.5  # neutral when no history
        scores.append(credit_spreads(pctile_2y, mom_bps))

    # VIX sentiment (sentiment block) — simplified: use level vs 20 threshold
    if vixcls is not None:
        # Treat VIX > 20 as trending-intact-but-fearful; VIX > 30 as break
        trend_ok = vixcls < 25.0
        # Approximate VIX3M/VIX ratio: use 1.05 when calm, 0.95 when elevated
        ratio = 0.95 if vixcls > 20 else 1.05
        scores.append(vix_term_structure(ratio, trend_ok))

    # Sahm rule (macro block) — degrade if not available
    if sahm_val is not None:
        # Approximate Sahm: uses unemployment rate directly only as proxy
        scores.append(sahm_rule(max(0.0, sahm_val - 3.5) * 0.5))
    else:
        scores.append(Score("macro.sahm", 0.0, degraded=True))

    block_weights = config.regime_block_weights or {"macro": 0.60, "sentiment": 0.40}
    comp_score, _detail = composite(scores, block_weights)

    # Detect credit-spread veto
    veto = any(
        s.name == "macro.credit_spreads" and s.meta.get("veto", 0) == 1.0
        for s in scores
    )

    # Load, transition, save regime state
    db_state = load_state(session)
    fsm_in = RegimeFSMInput(
        composite_score=comp_score,
        credit_spread_veto=veto,
        current_tilt=db_state.tilt,
        pending_tilt=db_state.pending_tilt,
        confirmation_count=db_state.confirmation_count,
    )
    fsm_out = transition(fsm_in)
    if persist_regime:
        save_state(session, fsm_out, comp_score)

    # Pick sleeve targets based on active tilt
    tilt = fsm_out.tilt
    if tilt == "defensive" and config.defensive_sleeve_targets:
        sleeve_targets = config.defensive_sleeve_targets
    elif tilt == "aggressive" and config.aggressive_sleeve_targets:
        sleeve_targets = config.aggressive_sleeve_targets
    else:
        sleeve_targets = config.sleeve_targets

    abstained = not scores or all(s.degraded for s in scores)
    return OracleOutput(
        oracle_id=config.id,
        display_name=config.display_name,
        per_holding_scores={},  # Macro Tactician doesn't score individual holdings
        sleeve_targets=sleeve_targets,
        persona_constraints=_build_persona_constraints(config.persona_constraints),
        abstained=abstained,
        abstain_reason="no_macro_data" if abstained else None,
    )


def run_oracle(
    config: OracleConfig,
    session: Session,
    scenario: ScenarioContext | None = None,
    active_instruments: dict[int, Instrument] | None = None,
    all_metrics: dict[int, dict[str, float | None]] | None = None,
) -> OracleOutput:
    """Run one oracle and return its output. No trades emitted (Invariant A).

    active_instruments and all_metrics may be pre-computed by run_all_oracles to
    avoid repeating the same bulk DB queries six times per convene.
    """
    if config.id == "macro_tactician":
        return _run_macro_tactician(
            config, session, scenario=scenario, persist_regime=(scenario is None)
        )

    if active_instruments is None:
        active_instruments = _fetch_active_instruments(session)
    if not active_instruments:
        return OracleOutput(
            oracle_id=config.id,
            display_name=config.display_name,
            per_holding_scores={},
            sleeve_targets=config.sleeve_targets,
            persona_constraints=_build_persona_constraints(config.persona_constraints),
            abstained=True,
            abstain_reason="no_holdings_or_universe",
        )

    # Apply universe filter
    scored_instruments: dict[int, Instrument] = {}
    for iid, inst in active_instruments.items():
        match config.universe_filter:
            case "equity_only":
                if inst.instrument_type not in _FUND_TYPES and inst.instrument_type == "stock":
                    scored_instruments[iid] = inst
            case "fund_only":
                if inst.instrument_type in _FUND_TYPES:
                    scored_instruments[iid] = inst
            case _:
                scored_instruments[iid] = inst

    if not scored_instruments and config.universe_filter:
        return OracleOutput(
            oracle_id=config.id,
            display_name=config.display_name,
            per_holding_scores={},
            sleeve_targets=config.sleeve_targets,
            persona_constraints=_build_persona_constraints(config.persona_constraints),
            abstained=True,
            abstain_reason=f"no_instruments_match_filter:{config.universe_filter}",
        )

    if all_metrics is None:
        all_metrics = compute_universe_metrics(session, scenario=scenario)

    # Build universe raw values per metric for percentile computation
    universe_raw: dict[str, list[float]] = {}
    for iid in scored_instruments:
        raw = all_metrics.get(iid, {})
        for metric_id in config.metric_weights:
            v = raw.get(metric_id)
            if v is not None:
                universe_raw.setdefault(metric_id, []).append(v)

    # Score each instrument
    per_holding_scores: dict[int, HoldingScore] = {}
    for iid, inst in scored_instruments.items():
        raw = all_metrics.get(iid, {})
        score, metric_scores, reasons = score_holding(
            instrument_id=iid,
            instrument_type=inst.instrument_type,
            raw_metrics=raw,
            universe_raw=universe_raw,
            metric_weights=config.metric_weights,
            abstain_floor=config.abstain_floor,
        )
        per_holding_scores[iid] = HoldingScore(
            instrument_id=iid,
            score=score,
            reasons=reasons,
            metrics=metric_scores,
        )

    return OracleOutput(
        oracle_id=config.id,
        display_name=config.display_name,
        per_holding_scores=per_holding_scores,
        sleeve_targets=config.sleeve_targets,
        persona_constraints=_build_persona_constraints(config.persona_constraints),
    )


def run_all_oracles(
    session: Session,
    scenario: ScenarioContext | None = None,
) -> list[OracleOutput]:
    """Run all six oracles and return their outputs.

    Bulk-fetches active instruments and metrics once, then passes to each oracle
    to avoid repeating the same queries six times.
    """
    active_instruments = _fetch_active_instruments(session)
    all_metrics = compute_universe_metrics(session, scenario=scenario)

    outputs = []
    for oracle_id in ORACLE_IDS:
        config = load_oracle_config(oracle_id)
        outputs.append(run_oracle(
            config, session, scenario=scenario,
            active_instruments=active_instruments,
            all_metrics=all_metrics,
        ))
    return outputs


def run_macro_tactician_pit(
    config: OracleConfig,
    session: Session,
    pit_date: date,
    fsm_state: RegimeFSMInput | None = None,
) -> tuple[OracleOutput, RegimeFSMOutput]:
    """Backtest-only PIT variant of the Macro Tactician.

    Uses only data stamped ≤ pit_date.  When fsm_state is provided (carry-forward
    from the previous replay step), uses it instead of loading from DB.
    Never persists regime state — safe to call in a tight loop.
    """
    def _last_value(series_id: str) -> float | None:
        val = session.execute(
            select(MarketObservation.value)
            .where(
                MarketObservation.series_id == series_id,
                MarketObservation.degraded == False,  # noqa: E712
                MarketObservation.observed_date <= pit_date,
            )
            .order_by(MarketObservation.observed_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        return float(val) if val is not None else None

    def _last_two_values(series_id: str) -> tuple[float | None, float | None]:
        rows = session.execute(
            select(MarketObservation.value)
            .where(
                MarketObservation.series_id == series_id,
                MarketObservation.degraded == False,  # noqa: E712
                MarketObservation.observed_date <= pit_date,
            )
            .order_by(MarketObservation.observed_date.desc())
            .limit(2)
        ).scalars().all()
        if len(rows) >= 2:
            return float(rows[0]), float(rows[1])
        if len(rows) == 1:
            return float(rows[0]), None
        return None, None

    t10y3m = _last_value("T10Y3M")
    hy_oas_cur, hy_oas_prev = _last_two_values("BAMLH0A0HYM2")
    vixcls = _last_value("VIXCLS")
    sahm_val = _last_value("UNRATE")

    scores: list[Score] = []

    if t10y3m is not None:
        scores.append(yield_curve(t10y3m * 100.0))

    if hy_oas_cur is not None:
        mom_bps = (hy_oas_cur - hy_oas_prev) * 100.0 if hy_oas_prev is not None else 0.0
        obs_2y = session.execute(
            select(MarketObservation.value)
            .where(
                MarketObservation.series_id == "BAMLH0A0HYM2",
                MarketObservation.observed_date <= pit_date,
            )
            .order_by(MarketObservation.observed_date.desc())
            .limit(504)
        ).scalars().all()
        if obs_2y:
            vals_2y = sorted(float(v) for v in obs_2y)
            n2 = len(vals_2y)
            below = sum(1 for v in vals_2y if v < hy_oas_cur)
            pctile_2y = (below + 0.5) / n2
        else:
            pctile_2y = 0.5
        scores.append(credit_spreads(pctile_2y, mom_bps))

    if vixcls is not None:
        ratio = 0.95 if vixcls > 20 else 1.05
        scores.append(vix_term_structure(ratio, vixcls < 25.0))

    if sahm_val is not None:
        scores.append(sahm_rule(max(0.0, sahm_val - 3.5) * 0.5))
    else:
        scores.append(Score("macro.sahm", 0.0, degraded=True))

    block_weights = config.regime_block_weights or {"macro": 0.60, "sentiment": 0.40}
    comp_score, _ = composite(scores, block_weights)

    veto = any(s.name == "macro.credit_spreads" and s.meta.get("veto", 0) == 1.0 for s in scores)

    if fsm_state is not None:
        current_tilt = fsm_state.current_tilt
        pending_tilt = fsm_state.pending_tilt
        confirmation_count = fsm_state.confirmation_count
    else:
        db_state = load_state(session)
        current_tilt = db_state.tilt
        pending_tilt = db_state.pending_tilt
        confirmation_count = db_state.confirmation_count

    fsm_in = RegimeFSMInput(
        composite_score=comp_score,
        credit_spread_veto=veto,
        current_tilt=current_tilt,
        pending_tilt=pending_tilt,
        confirmation_count=confirmation_count,
    )
    fsm_out = transition(fsm_in)

    tilt = fsm_out.tilt
    if tilt == "defensive" and config.defensive_sleeve_targets:
        sleeve_targets = config.defensive_sleeve_targets
    elif tilt == "aggressive" and config.aggressive_sleeve_targets:
        sleeve_targets = config.aggressive_sleeve_targets
    else:
        sleeve_targets = config.sleeve_targets

    abstained = not scores or all(s.degraded for s in scores)
    oracle_out = OracleOutput(
        oracle_id=config.id,
        display_name=config.display_name,
        per_holding_scores={},
        sleeve_targets=sleeve_targets,
        persona_constraints=_build_persona_constraints(config.persona_constraints),
        abstained=abstained,
        abstain_reason="no_macro_data" if abstained else None,
    )
    return oracle_out, fsm_out
