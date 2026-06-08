"""Constraint profile loader.

Profiles live in rebalancer/constraints/*.yaml.
The rebalancer enforces BOTH the persona_constraints (from the oracle output)
AND the user-selected constraint profile.  These are different constraint sources.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_CONSTRAINTS_DIR = Path(__file__).parent / "constraints"


@dataclass
class ConstraintProfile:
    id: str
    display_name: str
    description: str
    # Empty list = unconstrained (any instrument type may be bought).
    allowed_buy_types: list[str]

    def can_buy(self, instrument_type: str | None) -> bool:
        if not self.allowed_buy_types:
            return True
        return instrument_type in self.allowed_buy_types


def load_constraint_profile(profile_id: str) -> ConstraintProfile:
    path = _CONSTRAINTS_DIR / f"{profile_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Constraint profile not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return ConstraintProfile(
        id=data["id"],
        display_name=data["display_name"],
        description=data.get("description", ""),
        allowed_buy_types=data.get("allowed_buy_types", []),
    )


PROFILE_IDS = ["unconstrained", "funds_only"]
