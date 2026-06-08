"""Shared types for oracle output and rebalancer input.

Both `oracles/` and `rebalancer/` import from here.  Neither imports the other.
Scores stay float (dimensionless); sleeve targets and dollar amounts use Decimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# First-class missing-data sentinel — never substitute a fake number.
NA = "n/a"
MetricValue = float | str  # float in [-1,+1] or "n/a"


@dataclass
class HoldingScore:
    instrument_id: int
    score: float | None  # None = abstain (too few applicable metrics)
    reasons: list[str]  # e.g. ["pe_ratio=18.5 (p25, s=+0.50)"]
    metrics: dict[str, MetricValue]  # metric_name → score | "n/a"


@dataclass
class PersonaConstraints:
    min_yield: Decimal | None = None  # Yield Harvester
    max_positions: int | None = None  # Growth Visionary
    individual_stock_target: Decimal | None = None  # Pragmatist: Decimal("0")
    min_score_to_buy: float | None = None  # abstain from buying below this


@dataclass
class OracleOutput:
    oracle_id: str
    display_name: str
    per_holding_scores: dict[int, HoldingScore]  # instrument_id → score
    sleeve_targets: dict[str, Decimal]  # sleeve → target weight (sum ≈ 1)
    persona_constraints: PersonaConstraints
    abstained: bool = False
    abstain_reason: str | None = None


@dataclass(frozen=True)
class ScenarioContext:
    """Immutable shock vector for a single scenario run.

    sleeve_shocks: sleeve → fractional change (Decimal("-0.30") = −30%).
    indicator_overrides: FRED series_id → override float value used instead of DB.
    """

    pack_id: str
    sleeve_shocks: dict[str, Decimal] = field(default_factory=dict)
    indicator_overrides: dict[str, float] = field(default_factory=dict)


@dataclass
class TradeProposal:
    instrument_id: int
    account_id: str
    direction: str  # "buy" | "sell"
    qty: Decimal  # whole shares
    estimated_value: Decimal
    rationale_tags: list[str]  # ["drift", "score", "constraint"]
    oracle_score: float | None
    tax_note: str | None = None
