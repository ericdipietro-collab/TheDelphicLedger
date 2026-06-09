"""Pydantic response schemas for the dashboard API.

Decimal → str to avoid float precision loss (Invariant E).
Absent metrics → null/None, never a fabricated value (Invariant H).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, PlainSerializer

# All money/quantity fields serialize as strings (Invariant E: no float for money).
DecimalStr = Annotated[Any, PlainSerializer(lambda v: str(v) if v is not None else None, return_type=str | None)]


# ── Run / Chamber ──────────────────────────────────────────────────────────────

class RunSummary(BaseModel):
    run_id: str
    run_at: datetime
    oracle_count: int
    proposal_count: int
    scenario_id: str | None
    regime_state: str | None


class HoldingScoreOut(BaseModel):
    instrument_id: int
    ticker: str | None = None
    score: float | None
    reasons: list[str]


class OracleCard(BaseModel):
    oracle_id: str
    display_name: str
    scored_count: int
    abstained: bool
    abstain_reason: str | None
    sleeve_targets: dict[str, DecimalStr]
    top_scores: list[HoldingScoreOut]  # top 3 by score
    regime_state: str | None
    proposal_count: int


class DissentCell(BaseModel):
    oracle_id: str
    direction: str | None  # "buy" | "sell" | "hold" | None (no proposal)
    qty: DecimalStr
    score: float | None


class DissentRow(BaseModel):
    instrument_id: int
    ticker: str | None
    name: str | None
    cells: list[DissentCell]  # one per oracle, in ORACLE_IDS order


class RivalObjection(BaseModel):
    oracle_id: str
    rival_id: str
    instrument_id: int
    ticker: str | None
    oracle_direction: str
    rival_direction: str


class ChamberResponse(BaseModel):
    run_id: str
    run_at: datetime
    scenario_id: str | None
    oracle_cards: list[OracleCard]
    dissent_matrix: list[DissentRow]
    rival_objections: list[RivalObjection]


# ── Portfolio ──────────────────────────────────────────────────────────────────

class SleeveAllocation(BaseModel):
    sleeve: str
    market_value: DecimalStr
    weight: float  # 0–1


class DriftGauge(BaseModel):
    sleeve: str
    actual_weight: float
    target_weight: float  # from selected oracle
    drift_abs: float       # actual − target
    outside_band: bool


class HoldingRow(BaseModel):
    instrument_id: int
    ticker: str | None
    name: str | None
    instrument_type: str | None
    sleeve: str | None
    account_id: str | None
    qty: DecimalStr
    market_value: DecimalStr
    oracle_scores: dict[str, float | None]  # oracle_id → score
    has_8k_flag: bool  # always False until EDGAR 8-K data wired; renders as n/a


class PortfolioResponse(BaseModel):
    as_of: str  # ISO date
    total_market_value: DecimalStr
    allocations: list[SleeveAllocation]
    drift_gauges: list[DriftGauge]
    holdings: list[HoldingRow]
    selected_oracle: str


# ── Trades ─────────────────────────────────────────────────────────────────────

class ProposalOut(BaseModel):
    instrument_id: int
    ticker: str | None
    name: str | None
    account_id: str
    direction: str
    qty: DecimalStr
    estimated_value: DecimalStr
    rationale_tags: list[str]
    oracle_score: float | None
    tax_note: str | None


class OracleProposals(BaseModel):
    oracle_id: str
    display_name: str
    proposals: list[ProposalOut]


class TradesResponse(BaseModel):
    run_id: str
    constraint: str | None
    oracle_proposals: list[OracleProposals]


# ── Scenarios ──────────────────────────────────────────────────────────────────

class PackSummary(BaseModel):
    pack_id: str
    display_name: str
    pack_type: str  # "historical" | "hypothetical"
    has_run: bool   # whether a scenario Decision row exists for this pack


class SleeveWaterfall(BaseModel):
    sleeve: str
    before_mv: DecimalStr
    shock_pct: float        # e.g. -0.30 for −30%
    after_mv: DecimalStr
    delta_mv: DecimalStr


class OracleVerdictDelta(BaseModel):
    oracle_id: str
    display_name: str
    scored_count_baseline: int
    scored_count_scenario: int
    abstained_baseline: bool
    abstained_scenario: bool


class ScenarioResult(BaseModel):
    pack_id: str
    display_name: str
    pack_type: str
    run_id: str
    run_at: datetime
    waterfall: list[SleeveWaterfall]
    verdict_deltas: list[OracleVerdictDelta]
    honesty_note: str | None


# ── Recon ──────────────────────────────────────────────────────────────────────

class ReconBreakOut(BaseModel):
    id: int
    ticker: str | None
    account_id: str
    as_of: str
    expected_qty: DecimalStr
    actual_qty: DecimalStr
    delta: DecimalStr
    status: str
    coverage_gap: bool
    suggested_cause: str | None
    resolution_note: str | None


class ReconResponse(BaseModel):
    open_count: int
    coverage_gap_count: int
    resolved_count: int
    breaks: list[ReconBreakOut]


# ── Config ─────────────────────────────────────────────────────────────────────

class ConfigResponse(BaseModel):
    drift_abs: float
    drift_rel: float
    min_trade_usd: float
    new_money: float
    available_profiles: list[str]
