"""Tests for the regime FSM — pure function, no DB needed."""

from committee.signals.regime import (
    RegimeFSMInput,
    transition,
)


def _state(
    composite: float,
    current: str = "neutral",
    pending: str | None = None,
    count: int = 0,
    veto: bool = False,
) -> RegimeFSMInput:
    return RegimeFSMInput(
        composite_score=composite,
        credit_spread_veto=veto,
        current_tilt=current,
        pending_tilt=pending,
        confirmation_count=count,
    )


# ── No-change cases ────────────────────────────────────────────────────────────

def test_neutral_stays_neutral_inside_bands():
    out = transition(_state(0.0))
    assert out.tilt == "neutral"
    assert not out.tilt_changed
    assert out.pending_tilt is None


def test_defensive_stays_defensive_below_exit():
    out = transition(_state(-0.20, current="defensive"))
    assert out.tilt == "defensive"
    assert not out.tilt_changed


def test_aggressive_stays_aggressive_above_exit():
    out = transition(_state(0.20, current="aggressive"))
    assert out.tilt == "aggressive"
    assert not out.tilt_changed


# ── Two-run confirmation ────────────────────────────────────────────────────────

def test_first_run_below_entry_sets_pending():
    out = transition(_state(-0.40))
    assert out.tilt == "neutral"
    assert out.pending_tilt == "defensive"
    assert out.confirmation_count == 1
    assert not out.tilt_changed


def test_second_consecutive_run_confirms_tilt():
    # First run: starts pending
    out1 = transition(_state(-0.40))
    assert out1.pending_tilt == "defensive"
    # Second run: same direction → confirm
    out2 = transition(_state(-0.40, pending=out1.pending_tilt, count=out1.confirmation_count))
    assert out2.tilt == "defensive"
    assert out2.tilt_changed
    assert out2.change_reason and "two_run_confirmation" in out2.change_reason


def test_reversed_signal_resets_pending():
    out = transition(_state(0.0, pending="defensive", count=1))
    assert out.pending_tilt is None
    assert out.confirmation_count == 0
    assert not out.tilt_changed


def test_new_direction_replaces_pending():
    # Was pending aggressive, but signal now says defensive
    out = transition(_state(-0.40, pending="aggressive", count=1))
    assert out.pending_tilt == "defensive"
    assert out.confirmation_count == 1


# ── Asymmetric exit bands ──────────────────────────────────────────────────────

def test_defensive_exit_requires_composite_above_minus_10():
    # Inside the defensive zone but above EXIT_DEFENSIVE band
    out = transition(_state(-0.05, current="defensive"))
    assert out.pending_tilt == "neutral"  # starts confirmation
    assert not out.tilt_changed


def test_defensive_stays_below_exit_threshold():
    out = transition(_state(-0.15, current="defensive"))
    assert out.tilt == "defensive"
    assert out.pending_tilt is None  # still defensive, no pending


def test_aggressive_exit_requires_composite_below_10():
    out = transition(_state(0.05, current="aggressive"))
    assert out.pending_tilt == "neutral"
    assert not out.tilt_changed


# ── Circuit breaker ────────────────────────────────────────────────────────────

def test_credit_spread_veto_immediately_defensive():
    out = transition(_state(0.10, veto=True))  # composite is fine but veto fires
    assert out.tilt == "defensive"
    assert out.tilt_changed
    assert out.change_reason == "credit_spread_circuit_breaker"
    assert out.confirmation_count == 0


def test_circuit_breaker_no_confirmation_needed():
    # Even from aggressive, single veto takes to defensive immediately
    out = transition(_state(0.50, current="aggressive", veto=True))
    assert out.tilt == "defensive"
    assert out.tilt_changed


def test_already_defensive_veto_no_change():
    out = transition(_state(-0.20, current="defensive", veto=True))
    # Already defensive — veto check fires but current == "defensive" so no change
    assert not out.tilt_changed
    assert out.tilt == "defensive"


# ── DB persistence ─────────────────────────────────────────────────────────────

def test_db_state_persists_across_transitions(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from committee.models import Base
    from committee.signals.regime import load_state, save_state

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        state = load_state(session)
        assert state.tilt == "neutral"

        fsm_in = RegimeFSMInput(
            composite_score=-0.40,
            credit_spread_veto=False,
            current_tilt=state.tilt,
            pending_tilt=state.pending_tilt,
            confirmation_count=state.confirmation_count,
        )
        out = transition(fsm_in)
        save_state(session, out, -0.40)
        session.commit()

    with Session(engine) as session:
        state2 = load_state(session)
        assert state2.pending_tilt == "defensive"
        assert state2.confirmation_count == 1
        assert abs(float(state2.composite_score) - (-0.40)) < 0.001
