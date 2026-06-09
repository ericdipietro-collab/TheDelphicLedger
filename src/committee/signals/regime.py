"""Macro Tactician regime state machine.

Pure transition function: RegimeFSMInput → RegimeFSMOutput.
DB layer: load_state() / save_state().

Asymmetric enter/exit bands (enter requires a stronger signal than exit):
  Enter defensive:   composite < -0.30
  Exit  defensive:   composite > -0.10
  Enter aggressive:  composite > +0.30
  Exit  aggressive:  composite < +0.10

Two-run confirmation before any tilt change.

Circuit breaker: credit-spread veto (meta["veto"]==1.0) → immediately
defensive, no confirmation.  Exiting a breaker-induced defensive tilt uses
the normal two-run process — no snap-back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from committee.models import RegimeState

ENTER_DEFENSIVE = -0.30
EXIT_DEFENSIVE = -0.10
ENTER_AGGRESSIVE = +0.30
EXIT_AGGRESSIVE = +0.10
CONFIRMATION_RUNS = 2

CONFIRMATION_WINDOW_DAYS = 14   # 14 calendar days ≈ 10 trading days
CONFIRMATION_DATES_REQUIRED = 2  # distinct observation dates required

_BASE_TARGETS: dict[str, str] = {
    "equity_us": "0.40",
    "equity_intl": "0.20",
    "fixed_income": "0.30",
    "alternatives": "0.05",
    "cash": "0.05",
}
_DEFENSIVE_TARGETS: dict[str, str] = {
    "equity_us": "0.30",
    "equity_intl": "0.15",
    "fixed_income": "0.45",
    "alternatives": "0.05",
    "cash": "0.05",
}
_AGGRESSIVE_TARGETS: dict[str, str] = {
    "equity_us": "0.55",
    "equity_intl": "0.25",
    "fixed_income": "0.10",
    "alternatives": "0.05",
    "cash": "0.05",
}

TILT_SLEEVE_TARGETS = {
    "neutral": _BASE_TARGETS,
    "defensive": _DEFENSIVE_TARGETS,
    "aggressive": _AGGRESSIVE_TARGETS,
}


@dataclass(frozen=True)
class RegimeFSMInput:
    composite_score: float
    credit_spread_veto: bool
    current_tilt: str  # "neutral" | "defensive" | "aggressive"
    pending_tilt: str | None
    confirmation_count: int


@dataclass(frozen=True)
class RegimeFSMOutput:
    tilt: str
    pending_tilt: str | None
    confirmation_count: int
    tilt_changed: bool
    change_reason: str | None


def _desired_tilt(composite: float, current: str) -> str:
    """Desired tilt from composite, using asymmetric entry/exit bands."""
    match current:
        case "defensive":
            return "neutral" if composite > EXIT_DEFENSIVE else "defensive"
        case "aggressive":
            return "neutral" if composite < EXIT_AGGRESSIVE else "aggressive"
        case _:  # "neutral"
            if composite < ENTER_DEFENSIVE:
                return "defensive"
            if composite > ENTER_AGGRESSIVE:
                return "aggressive"
            return "neutral"


def transition(state: RegimeFSMInput) -> RegimeFSMOutput:
    """Pure FSM transition — no DB access, no side effects."""
    # Circuit breaker: veto immediately goes defensive (no confirmation).
    if state.credit_spread_veto and state.current_tilt != "defensive":
        return RegimeFSMOutput(
            tilt="defensive",
            pending_tilt=None,
            confirmation_count=0,
            tilt_changed=True,
            change_reason="credit_spread_circuit_breaker",
        )

    desired = _desired_tilt(state.composite_score, state.current_tilt)

    if desired == state.current_tilt:
        return RegimeFSMOutput(
            tilt=state.current_tilt,
            pending_tilt=None,
            confirmation_count=0,
            tilt_changed=False,
            change_reason=None,
        )

    if desired == state.pending_tilt:
        new_count = state.confirmation_count + 1
        if new_count >= CONFIRMATION_RUNS:
            return RegimeFSMOutput(
                tilt=desired,
                pending_tilt=None,
                confirmation_count=0,
                tilt_changed=True,
                change_reason=f"two_run_confirmation:{desired}",
            )
        return RegimeFSMOutput(
            tilt=state.current_tilt,
            pending_tilt=desired,
            confirmation_count=new_count,
            tilt_changed=False,
            change_reason=None,
        )

    # New desired direction — start confirmation.
    return RegimeFSMOutput(
        tilt=state.current_tilt,
        pending_tilt=desired,
        confirmation_count=1,
        tilt_changed=False,
        change_reason=None,
    )


@dataclass(frozen=True)
class StatelessRegimeOutput:
    tilt: str                            # "neutral" | "defensive" | "aggressive"
    tilt_changed: bool
    change_reason: str | None
    confirmation_dates: list[date_type]  # dates that contributed to confirmation
    policy_version: str


def transition_stateless(
    observations: list[tuple[date_type, float]],
    current_tilt: str,
    as_of: date_type,
    policy_version: str,
    credit_spread_veto: bool = False,
) -> StatelessRegimeOutput:
    """Replay-stateless regime determination.

    Rules:
    - Considers only observations within a rolling 14-calendar-day window ending at as_of.
    - Requires >= 2 distinct observation dates where the entry condition holds.
    - Two observations on the same date count as one confirmation date.
    - Credit-spread circuit breaker: defensive immediately, no date confirmation needed.
    - Same inputs always produce same output (pure function, no DB access).
    """
    # Circuit breaker fires immediately — no date confirmation required.
    if credit_spread_veto and current_tilt != "defensive":
        return StatelessRegimeOutput(
            tilt="defensive",
            tilt_changed=True,
            change_reason="credit_spread_circuit_breaker",
            confirmation_dates=[as_of],
            policy_version=policy_version,
        )

    window_start = as_of - timedelta(days=CONFIRMATION_WINDOW_DAYS)

    # Collect unique dates where the entry condition (desired != current) holds.
    unique_confirmation_dates: set[date_type] = set()
    last_desired: str = current_tilt
    for obs_date, composite in sorted(observations):
        if obs_date < window_start or obs_date > as_of:
            continue
        desired = _desired_tilt(composite, current_tilt)
        if desired != current_tilt:
            unique_confirmation_dates.add(obs_date)
            last_desired = desired

    sorted_conf_dates = sorted(unique_confirmation_dates)

    if len(unique_confirmation_dates) >= CONFIRMATION_DATES_REQUIRED:
        new_tilt = last_desired
        if new_tilt != current_tilt:
            return StatelessRegimeOutput(
                tilt=new_tilt,
                tilt_changed=True,
                change_reason=f"date_confirmation:{new_tilt}",
                confirmation_dates=sorted_conf_dates,
                policy_version=policy_version,
            )

    return StatelessRegimeOutput(
        tilt=current_tilt,
        tilt_changed=False,
        change_reason=None,
        confirmation_dates=sorted_conf_dates,
        policy_version=policy_version,
    )


def load_state(session: Session) -> RegimeState:
    """Load (or bootstrap) the single regime state row."""
    state = session.get(RegimeState, 1)
    if state is None:
        state = RegimeState(
            id=1,
            tilt="neutral",
            composite_score=None,
            pending_tilt=None,
            confirmation_count=0,
        )
        session.add(state)
        session.flush()
    return state


def save_state(session: Session, fsm_out: RegimeFSMOutput, composite: float) -> None:
    state = load_state(session)
    state.tilt = fsm_out.tilt
    state.composite_score = composite
    state.pending_tilt = fsm_out.pending_tilt
    state.confirmation_count = fsm_out.confirmation_count
    state.last_run_at = datetime.now()
