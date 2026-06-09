"""Golden-file tests for replay-stateless regime (AC-1, AC-2)."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from committee.signals.regime import transition_stateless

GOLDEN_DIR = Path(__file__).parent / "goldens"
GOLDEN_DIR.mkdir(exist_ok=True)


def _run_and_golden(name: str, **kwargs) -> None:
    result = transition_stateless(**kwargs)
    actual = {
        "tilt": result.tilt,
        "tilt_changed": result.tilt_changed,
        "change_reason": result.change_reason,
        "policy_version": result.policy_version,
    }
    golden_path = GOLDEN_DIR / f"macro_{name}.json"
    if not golden_path.exists():
        golden_path.write_text(json.dumps(actual, indent=2))
        pytest.skip(f"Golden created: {golden_path}")
    expected = json.loads(golden_path.read_text())
    assert actual == expected, f"Golden mismatch for {name}: {actual} != {expected}"


def test_golden_neutral():
    _run_and_golden(
        "neutral",
        observations=[(date(2024, 1, 2), 0.05), (date(2024, 1, 3), 0.05)],
        current_tilt="neutral",
        as_of=date(2024, 1, 10),
        policy_version="v1",
    )


def test_golden_defensive():
    _run_and_golden(
        "defensive",
        observations=[(date(2024, 1, 2), -0.40), (date(2024, 1, 3), -0.40)],
        current_tilt="neutral",
        as_of=date(2024, 1, 10),
        policy_version="v1",
    )


def test_golden_aggressive():
    _run_and_golden(
        "aggressive",
        observations=[(date(2024, 1, 2), 0.40), (date(2024, 1, 3), 0.40)],
        current_tilt="neutral",
        as_of=date(2024, 1, 10),
        policy_version="v1",
    )


def test_golden_circuit_breaker():
    _run_and_golden(
        "circuit_breaker",
        observations=[(date(2024, 1, 2), 0.40)],
        current_tilt="neutral",
        as_of=date(2024, 1, 2),
        policy_version="v1",
        credit_spread_veto=True,
    )
