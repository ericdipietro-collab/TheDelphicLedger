from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from committee.types import DecimalText


class Base(DeclarativeBase):
    pass


class ImportBatch(Base):
    """Immutable record of a raw file import. Duplicate hash = no-op."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(Text, nullable=False)  # "positions" | "transactions"
    broker_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    template_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    position_snapshots: Mapped[list[PositionSnapshot]] = relationship(back_populates="batch")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="batch")


class Account(Base):
    """Reference table for brokerage accounts."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    broker: Mapped[str | None] = mapped_column(Text, nullable=True)
    tax_type: Mapped[str | None] = mapped_column(Text, nullable=True)  # taxable|trad|roth|401k
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Instrument(Base):
    """Canonical instrument record. Populated by the resolver (M1b)."""

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    figi: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_class: Mapped[str | None] = mapped_column(Text, nullable=True)
    sleeve: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_cash_equivalent: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)
    needs_unwind: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)
    aliases: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    bundle_tags: Mapped[Any] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class PositionSnapshot(Base):
    """Immutable snapshot of one holding at one point in time."""

    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), nullable=True
    )
    raw_instrument: Mapped[str | None] = mapped_column(Text, nullable=True)
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    market_value: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    cost_basis: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)

    batch: Mapped[ImportBatch] = relationship(back_populates="position_snapshots")
    instrument: Mapped[Instrument | None] = relationship()


class Transaction(Base):
    """Immutable transaction record."""

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id"), nullable=False)
    account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), nullable=True
    )
    raw_instrument: Mapped[str | None] = mapped_column(Text, nullable=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    settle_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    raw_type: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    qty: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    price: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    fees: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)

    batch: Mapped[ImportBatch] = relationship(back_populates="transactions")
    instrument: Mapped[Instrument | None] = relationship()


class MappingDecision(Base):
    """Audit row for every instrument resolution or mapping choice."""

    __tablename__ = "mapping_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), nullable=True)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(DecimalText, nullable=True)  # type: ignore[assignment]
    accepted_by: Mapped[str | None] = mapped_column(Text, nullable=True)  # "auto" | "human"
    decided_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class UnresolvedQueue(Base):
    """Instruments or transaction types awaiting human resolution."""

    __tablename__ = "unresolved_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    queue_type: Mapped[str] = mapped_column(Text, nullable=False)  # "instrument" | "txn_type"
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_to: Mapped[str | None] = mapped_column(Text, nullable=True)


class TaxLot(Base):
    """Individual tax lots (populated in M7)."""

    __tablename__ = "lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(Text, nullable=False)
    acquired_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    cost_per_share: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    basis_quality: Mapped[str] = mapped_column(Text, nullable=False)  # "exact" | "average_fallback"
    origin: Mapped[str | None] = mapped_column(
        Text, nullable=True, default="broker_derived"
    )  # "broker_derived" | "snapshot_fallback" | "user_asserted" | "user_corrected"


class LotCorrection(Base):
    """Append-only tax-lot correction. Never mutate or delete rows.

    User corrections override broker-derived lots in derive_lots() (FR-4.7).
    validation_status: "active" | "conflicted" | "superseded" | "invalid"
    """

    __tablename__ = "lot_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supersedes_id: Mapped[int | None] = mapped_column(
        ForeignKey("lot_corrections.id"), nullable=True
    )
    account_key: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    acquired_date: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    cost_per_share: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    basis_quality: Mapped[str] = mapped_column(Text, nullable=False)  # "user_asserted" | "user_corrected"
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    validation_status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )

    instrument: Mapped[Instrument] = relationship()


class SleeveConfig(Base):
    """Versioned custom sleeve configuration. Immutable once created."""

    __tablename__ = "sleeve_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")  # active | inactive
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    definitions: Mapped[list[SleeveDefinition]] = relationship(back_populates="config")
    assignments: Mapped[list[SleeveAssignment]] = relationship(back_populates="config")


class SleeveDefinition(Base):
    """One sleeve within a SleeveConfig."""

    __tablename__ = "sleeve_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("sleeve_configs.id"), nullable=False)
    sleeve_key: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    target_weight: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    config: Mapped[SleeveConfig] = relationship(back_populates="definitions")


class SleeveAssignment(Base):
    """Maps an instrument to exactly one sleeve in a SleeveConfig."""

    __tablename__ = "sleeve_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("sleeve_configs.id"), nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    sleeve_key: Mapped[str] = mapped_column(Text, nullable=False)

    config: Mapped[SleeveConfig] = relationship(back_populates="assignments")
    instrument: Mapped[Instrument] = relationship()

    __table_args__ = (
        Index("ix_sleeve_assign_config_instrument", "config_id", "instrument_id", unique=True),
    )


class Holding(Base):
    """Derived holdings view — always recomputed, never a source of truth."""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    account_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    market_value: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    derived_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class ReconBreak(Base):
    """Quantity reconciliation break between projected and actual positions."""

    __tablename__ = "recon_breaks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(Text, nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    expected_qty: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    actual_qty: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    delta: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # "open" | "resolved" | "auto_closed"
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    coverage_gap: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)
    # "missed_reinvest" | "unrecorded_transfer" | "split" | "import_gap" | "immaterial"
    suggested_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InstrumentEvent(Base):
    """Corporate action that adjusts share quantity (split, reverse split)."""

    __tablename__ = "instrument_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    # "split" | "reverse_split"
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    ratio: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)  # new_qty / old_qty
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class MarketObservation(Base):
    """Append-only market data: prices, macro indicators, dividends."""

    __tablename__ = "market_observations"
    __table_args__ = (
        # Covers the three hot query patterns in metrics.py and engine.py:
        # 1. Latest price per instrument (instrument_id, unit, observed_date DESC)
        # 2. Price history range scan (same columns + date range)
        # 3. Freshness check in fetch-prices (instrument_id, unit)
        Index("ix_mo_instrument_unit_date", "instrument_id", "unit", "observed_date"),
        # FRED/macro series queries (series_id, observed_date)
        Index("ix_mo_series_date", "series_id", "observed_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    series_id: Mapped[str] = mapped_column(Text, nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    observed_date: Mapped[date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    value: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    degraded: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)


class Fundamental(Base):
    """Point-in-time fundamental data from EDGAR."""

    __tablename__ = "fundamentals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    filed_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(DecimalText, nullable=False)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class RegimeState(Base):
    """Single-row table tracking the Macro Tactician's regime tilt across runs."""

    __tablename__ = "regime_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    tilt: Mapped[str] = mapped_column(Text, nullable=False, default="neutral")
    composite_score: Mapped[float | None] = mapped_column(DecimalText, nullable=True)  # type: ignore[assignment]
    pending_tilt: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Decision(Base):
    """Audit row for every committee run — one row per oracle per run_id."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    oracle_name: Mapped[str] = mapped_column(Text, nullable=False)
    persona_key: Mapped[str] = mapped_column(Text, nullable=False)
    inputs_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    outputs_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    proposals_json: Mapped[Any] = mapped_column(JSON, nullable=True)
    regime_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)


class BundleState(Base):
    """Mutable state for each bundle (enabled/disabled, last refresh)."""

    __tablename__ = "bundle_state"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Integer, nullable=False, default=False)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    instrument_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UniverseEntry(Base):
    """Instruments available as buy candidates even when not currently held."""

    __tablename__ = "universe"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), nullable=False, unique=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class Deviation(Base):
    """User deviation from committee recommendation (M7)."""

    __tablename__ = "deviations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("decisions.id"), nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    committee_action: Mapped[str] = mapped_column(Text, nullable=False)
    user_action: Mapped[str] = mapped_column(Text, nullable=False)
    reason_type: Mapped[str] = mapped_column(Text, nullable=False)
    reason_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class ProviderPayload(Base):
    """Immutable archive of a raw provider response.

    New records on each fetch; never update existing records (Invariant B, FR-2.4).
    """

    __tablename__ = "provider_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_name: Mapped[str] = mapped_column(Text, nullable=False)  # "edgar" | "yfinance" | "fred"
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)  # URL or dataset ID
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # filing date if known
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)  # SHA-256 hex
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload: Mapped[Any] = mapped_column(JSON, nullable=False)


class NormalizedFact(Base):
    """Normalized fact derived from a provider payload.

    Retains field-level provenance to the archived payload (FR-2.3).
    New records on provider revision (FR-2.4).
    """

    __tablename__ = "normalized_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload_id: Mapped[int] = mapped_column(ForeignKey("provider_payloads.id"), nullable=False)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    fact_key: Mapped[str] = mapped_column(Text, nullable=False)  # e.g. "pe_ratio", "roic"
    period: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. "2023-12-31"
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. "ratio", "USD"
    value: Mapped[str] = mapped_column(Text, nullable=False)  # serialized decimal string
    normalization_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    payload: Mapped[ProviderPayload] = relationship()
    instrument: Mapped[Instrument | None] = relationship()
