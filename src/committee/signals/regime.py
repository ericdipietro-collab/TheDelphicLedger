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
from datetime import datetime

from sqlalchemy.orm import Session

from committee.models import RegimeState

ENTER_DEFENSIVE = -0.30
EXIT_DEFENSIVE = -0.10
ENTER_AGGRESSIVE = +0.30
EXIT_AGGRESSIVE = +0.10
CONFIRMATION_RUNS = 2

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
