# DelphiLedger Enhancements & Simplifications

**Status:** Approved for implementation
**Owner:** Eric DiPietro
**Last updated:** 2026-06-09

## 1. Summary

This PRD defines a focused set of enhancements to simplify DelphiLedger's operation without weakening its auditability, determinism, financial precision, or portfolio-analysis model.

The proposed changes are:

1. Make the Macro Tactician reproducible without persisted run-count state.
2. Add a local provider archive with field-level provenance; free-source adapters only (paid normalized providers are out of scope).
3. Add user-defined household analysis sleeves without mixing them with account placement.
4. Add a tax-lot correction workflow without mutating imported or derived records.
5. Enforce decimal-safe financial handling throughout the React frontend.
6. Export trade proposals as broker-compatible batch CSV files.

## 2. Product Context

DelphiLedger is a local-only, deterministic portfolio analysis tool. It imports immutable brokerage records, derives household holdings and tax lots, enriches them with market data, and produces theoretical trades through deterministic oracle and rebalancer logic.

### Core Invariants

All requirements in this PRD must preserve the following invariants:

1. **Stateless Oracles:** Oracle outputs are reproducible functions of explicit, dated inputs and versioned policy. Individual security scores never depend on prior runs.
2. **Immutability:** Imported snapshots, transactions, raw provider payloads, and historical decisions are append-only and never edited in place.
3. **Household Analysis vs. Account Placement:** Allocation analysis occurs at household rollup; the rebalancer places trades per account.
4. **Deterministic Math:** No LLM participates in scoring, regime selection, lot derivation, or trade generation.
5. **Financial Precision:** Monetary values, quantities, prices, rates, and allocation weights use decimal-safe representations. Floating-point arithmetic is not authoritative.

## 3. Goals

- Produce identical oracle and trade outputs when rerun with identical dated inputs and configuration versions.
- Reduce operational fragility from stateful regime transitions and raw-provider complexity.
- Support user-defined allocation taxonomies without creating account-specific analysis results.
- Allow users to correct incomplete or erroneous tax-lot data while preserving a complete audit trail.
- Prevent precision loss between the Python engine, JSON API, and React dashboard.
- Preserve local replay and historical-decision reproducibility when external providers revise data.

## 4. Non-Goals

- Broker integration or trade execution.
- Intraday analysis.
- Automatic tax filing or authoritative tax advice.
- Account-specific oracle scores or account-specific target allocations.
- Editing or deleting imported transactions, snapshots, raw provider payloads, or historical decisions.
- Supporting arbitrary overlapping portfolio classifications in the initial custom-sleeves release.
- Making charts or browser-side calculations authoritative for trade generation.

## 5. Architectural Decisions

### 5.1 Macro Tactician Becomes Replay-Stateless

The Macro Tactician will no longer transition based on the number of times a user runs the application. Its output will be a pure function of:

- market observations available through an explicit decision date;
- a versioned regime policy;
- an explicit scenario override, when applicable.

Smoothing alone will not replace hysteresis. The regime policy will retain:

- smoothed indicators where validated;
- neutral dead bands;
- asymmetric entry and exit thresholds;
- confirmation across distinct observation dates;
- an immediate defensive circuit breaker for approved stress indicators.

**Confirmation rule (decided):** a tilt change requires the entry (or exit) condition to hold on at least two distinct observation dates within a rolling 10-trading-day window ending at `as_of`. Observation dates come from the market data itself, never from invocation timestamps. Two observations sharing a date count as one confirmation date.

**Circuit breaker (decided):** defensive-only. It may move the tilt toward defensive immediately, without multi-date confirmation, but may never accelerate a move toward an aggressive tilt. Approved trigger indicators: credit spreads (FRED high-yield OAS) and VIX, with thresholds defined in the versioned regime policy.

The same observation history and policy version must always produce the same tilt, regardless of invocation count or prior application state.

### 5.2 Normalized Fundamentals Are an Adapter, Not a New Source of Truth

**Decided:** SEC EDGAR remains the primary and mandatory provenance source for supported US issuer facts — it is free and authoritative. Only free data sources are eligible as providers; paid normalized providers (Tiingo paid tiers, etc.) are out of scope. The existing free sources (EDGAR XBRL, yfinance, FRED) are wrapped behind the common adapter contract; new free providers may be added later through the same contract.

Every provider response used by the system must be archived locally before normalization. Historical decisions must reference the archived payload and normalization-policy version used at decision time.

Free supplementary providers may act as:

- a fallback source;
- a source for instruments EDGAR does not cover (funds, ADRs without XBRL facts);
- a comparison source for data-quality checks.

The system must not silently replace previously used facts when a provider revises historical data.

### 5.3 Custom Sleeves Are Household Analysis Configuration

Custom sleeves define the household-level allocation taxonomy used by oracles, reporting, scenarios, and drift calculations.

**Decided:** custom sleeves do not replace the built-in taxonomy. They exist as named alternative configurations; the built-in taxonomy remains the default and is always available.

Custom sleeves must not encode account placement rules. Account eligibility, tax treatment, and trade placement remain rebalancer concerns.

The initial release will require each instrument to resolve to exactly one active analysis sleeve. Overlapping sleeves and partial membership weights are out of scope.

Custom-sleeve configurations must be versioned so historical decisions retain their original taxonomy.

### 5.4 Tax-Lot Changes Are Append-Only Corrections

Users may correct or supplement incomplete lot information, but they may not directly edit:

- imported transactions;
- imported snapshots;
- derived `TaxLot` rows;
- prior lot corrections.

The product will store user changes as append-only lot assertions or corrections. Lot derivation will deterministically rebuild active lots from immutable source records plus the latest applicable corrections.

A correction may be superseded by a later correction but never mutated or deleted.

**Decided precedence:** when a broker import conflicts with an active user lot correction, the user correction wins by default. The conflict is still recorded and surfaced for review; the broker-derived value is preserved as an alternative, never discarded.

**Decided corporate-action scope:** splits, mergers, dividends, and return-of-capital payments must have explicit supported handling before user-entered lots feed tax-aware proposals. Unsupported actions degrade tax analysis for the affected lots rather than guessing.

### 5.5 Frontend Financial Values Remain Decimal-Safe

The API will serialize authoritative financial values as decimal strings. The frontend will construct decimal values directly from those strings.

Native JavaScript `number` values may be used only for non-authoritative presentation concerns, such as chart coordinates, after an explicit conversion at the visualization boundary.

No value converted to a JavaScript `number` may be sent back into the decision, trade, tax-lot, or persistence path as an authoritative financial value.

**Decided scale and rounding:** the authoritative decimal scale is 4 decimal places for money, quantities, prices, and weights, using `ROUND_HALF_EVEN` (banker's rounding). Display formatting may round further (e.g., currency at 2 places) but never feeds back into authoritative values. Backend and frontend share these constants from one configuration source each, kept in sync by a round-trip test.

### 5.6 Trade Proposal Export (Batch CSV)

To reduce human error during execution, the system will export theoretical trade proposals into broker-compatible batch formats. This provides a bridge between the tool's deterministic output and the user's manual execution at their brokerage.

## 6. Functional Requirements

### FR-1: Replay-Stateless Macro Regime

1. A Macro Tactician run must accept an explicit `as_of` date.
2. The engine must load only observations available on or before `as_of`.
3. Confirmation rules must use distinct observation dates, not invocation count: a tilt change requires its condition on at least two distinct observation dates within the rolling 10-trading-day window ending at `as_of` (see 5.1).
4. The regime result must include:
   - selected tilt;
   - composite score;
   - contributing signals;
   - degraded signals;
   - confirmation evidence;
   - circuit-breaker evidence;
   - regime-policy version.
5. Repeated runs with identical inputs must produce byte-equivalent normalized output.
6. Scenario runs must not persist or alter live regime behavior.
7. If required inputs are insufficient, the Macro Tactician must abstain or remain neutral according to the versioned policy.

### FR-2: Provider Adapter and Local Archive

1. Fundamentals providers must implement a common adapter contract. Only free sources are eligible; EDGAR remains mandatory and primary for supported US issuer facts.
2. Each provider fetch must persist:
   - provider name;
   - provider endpoint or dataset identifier;
   - retrieval timestamp;
   - effective or filing date;
   - raw payload;
   - payload hash;
   - parser version;
   - normalization-policy version.
3. Normalized facts must retain field-level provenance to the archived payload.
4. Provider revisions must create new records rather than update old records.
5. Historical decision replay must resolve the same archived facts originally used.
6. The UI must display the source and effective date of a fundamental fact.
7. Provider conflicts must be visible and resolved through an explicit, deterministic precedence policy.
8. Missing, stale, throttled, or unavailable providers must degrade cleanly without fabricating values.

### FR-3: Custom Sleeves

1. Users may create, rename, order, and deactivate custom sleeves.
2. Each active custom-sleeve configuration must have a stable identifier and version.
3. Each instrument must map to exactly one active analysis sleeve.
4. Unmapped or multiply mapped instruments must be rejected from authoritative drift and trade calculations until resolved.
5. Sleeve targets must:
   - use decimal values;
   - be non-negative;
   - sum exactly to `1` at the authoritative scale of 4 decimal places.
6. All oracle and scenario outputs must reference the custom-sleeve configuration version used.
7. Historical decisions must continue rendering with their original sleeve labels and mappings.
8. Account placement rules must remain separate from sleeve definitions.

### FR-4: Tax-Lot Correction Workflow

1. Users may add a lot correction for a specific account and instrument.
2. A correction must include:
   - account;
   - instrument;
   - acquired date;
   - quantity;
   - cost per share or total basis;
   - basis-quality classification;
   - effective date;
   - typed reason;
   - creation timestamp.
3. The system must retain the origin of each active lot:
   - broker-derived;
   - snapshot fallback;
   - user asserted;
   - user corrected.
4. Corrections must be append-only and supersedable.
5. Lot derivation must be deterministic and idempotent.
6. Conflicting corrections or corrections inconsistent with current holdings must create a visible validation issue.
7. A later broker import must not silently overwrite an active user correction. The user correction wins by default; the conflict is flagged for review with the broker value preserved as an alternative.
8. Tax-lot corrections must never change household analysis quantities; reconciliation and holdings remain sourced from transactions and snapshots.

### FR-5: Decimal-Safe Frontend

1. Authoritative API financial fields must be represented as strings.
2. The frontend must use one approved decimal library and centralized helpers for:
   - parsing;
   - addition and subtraction;
   - multiplication and division;
   - comparison;
   - rounding;
   - currency formatting;
   - percentage formatting.
3. Financial values must never pass through `parseFloat`, `Number`, unary `+`, or native arithmetic before authoritative use.
4. User-entered financial values must remain strings until validated and converted by the decimal library, and must never be stored as native numbers.
5. Form submissions must send normalized decimal strings.
6. Rounding mode (`ROUND_HALF_EVEN`) and authoritative scale (4 decimal places) must be centrally configured and consistent with the backend.
7. Chart components may receive converted numeric values only through dedicated display-only adapters.
8. Automated checks must detect prohibited native-number conversions on financial fields.

### FR-6: Trade Proposal Export (Batch CSV)

1. The system must provide a `committee export-trades` command.
2. The dashboard must include a "Download Batch Trades" action in the Trades view.
3. Supported formats must include:
   - Fidelity Batch Trade CSV;
   - Schwab Order Import CSV.
4. Exported records must include:
   - Account identifier;
   - Symbol;
   - Action (Buy/Sell);
   - Quantity;
   - Order Type (Market default);
   - Rationale tag (Audit reference).
5. The export must warn if any required broker-specific field (e.g., specific account number mapping) is missing from the `accounts` reference.

## 7. Data Model Requirements

The implementation should introduce or equivalent-model the following concepts:

### Provider Archive

- `provider_payloads`: immutable raw payload, source metadata, timestamps, hash, and parser version.
- `normalized_facts`: normalized fact value, period, units, provenance reference, and normalization-policy version.

### Custom Sleeve Taxonomy

- `sleeve_configs`: immutable/versioned configuration identity and status.
- `sleeve_definitions`: sleeve identifier, label, order, and config version.
- `sleeve_assignments`: instrument-to-sleeve mapping for a config version.

### Tax-Lot Corrections

- `lot_corrections`: append-only assertion with account and instrument grain.
- `supersedes_id`: optional reference to a prior correction.
- `validation_status`: active, conflicted, superseded, or invalid.

Existing derived holdings and tax-lot tables remain rebuildable outputs, not editable sources of truth.

## 8. Edge Cases and Required Behavior

| Edge Case | Required Behavior |
|---|---|
| Provider revises a historical fact | Store a new payload and fact version; preserve prior decision replay. |
| EDGAR and normalized provider disagree | Surface conflict and apply documented deterministic precedence. |
| Indicator is revised after decision date | Historical replay uses the version available at the decision date when available. |
| User repeatedly runs Macro Tactician without new data | Result remains unchanged. |
| Two observations share a date | They count as one confirmation date unless policy explicitly states otherwise. |
| Custom sleeve is renamed | Historical decisions retain the old version and label. |
| Instrument has no custom sleeve | Block authoritative drift/trade output for the affected configuration. |
| Instrument maps to multiple sleeves | Reject the configuration until resolved. |
| Sleeve targets do not sum to exactly `1` | Reject configuration save. |
| User correction conflicts with broker import | Preserve both; user correction stays active by default; flag the conflict for review. |
| Correction quantity exceeds current account holding | Mark invalid or conflicted; do not silently apply. |
| Wash-sale activity spans household accounts | Analyze replacement activity across all relevant household accounts while preserving account-level lots. |
| Corporate action changes basis | Supported actions (splits, mergers, dividends, return-of-capital) are handled explicitly; anything else degrades tax analysis for affected lots. |
| Foreign-currency lot basis is entered | Preserve native currency and required FX provenance; do not silently assume USD. |
| Decimal value exceeds chart-safe numeric range | Display through summarized or scaled presentation without affecting authoritative value. |

## 9. Acceptance Criteria

### AC-1: Deterministic Macro Replay

Given identical archived observations, `as_of` date, and regime-policy version, 100 repeated Macro Tactician runs produce identical normalized outputs and do not modify persistent regime state.

### AC-2: No Run-Count Confirmation

Repeated application runs against one observation date cannot satisfy a multi-date confirmation rule.

### AC-3: Historical Provider Replay

A historical decision produces the same normalized fundamentals after the external provider revises its current response.

### AC-4: Provider Failure Degradation

When a normalized provider is unavailable, the system uses an explicitly configured fallback or marks affected metrics degraded without fabricating values.

### AC-5: Sleeve Grain Integrity

The same instrument has one analysis sleeve across the household, while generated trades may still be placed differently across accounts.

### AC-6: Sleeve Version Replay

A historical decision remains renderable and reproducible after a sleeve is renamed, remapped, or deactivated in a later configuration version.

### AC-7: Immutable Lot Corrections

Creating, superseding, and rebuilding a tax-lot correction never updates or deletes imported transactions, snapshots, prior corrections, or historical decisions.

### AC-8: Lot Correction Isolation

Applying a tax-lot correction changes tax analysis but does not change household holdings quantity or market value.

### AC-9: Decimal Transport Integrity

Representative high-precision quantities and large monetary values survive backend-to-frontend-to-backend round trips without precision loss.

### AC-10: Visualization Isolation

Conversions to JavaScript `number` occur only in display adapters and cannot affect submitted parameters, persisted values, trade calculations, or tax calculations.

## 10. Validation and Testing Requirements

- Golden-file tests for Macro Tactician replay across neutral, defensive, aggressive, and circuit-breaker cases.
- Lookahead tests proving observations after `as_of` cannot influence a result.
- Backtests comparing current hysteresis and proposed replay-stateless policy on:
  - maximum drawdown;
  - CAGR;
  - switch count;
  - turnover;
  - response lag;
  - implied tax drag.
- Provider contract tests using archived fixtures.
- Replay tests proving historical decisions retain original provider and sleeve versions.
- Property tests for target-weight sums and decimal round trips.
- Immutability tests proving correction workflows cannot update source records.
- Household/account-grain tests proving custom sleeves do not influence account-specific analysis.
- Frontend static checks and tests prohibiting native-number financial operations outside visualization adapters.

## 11. Rollout Plan

Detailed work packages and sequencing live in §14 (Implementation Plan). The phase structure:

### Phase 0: Prerequisite — M8 Backtest Harness

- Build the backtest harness from the existing roadmap (M8). The Macro policy change is gated on the comparative backtests in §10, which require this harness.

### Phase 1: Foundations + Quick Win

- Add versioned policy/config identifiers to decision records.
- Define decimal transport contract (decimal strings in API) and frontend helper contracts.
- Ship FR-6 trade proposal export (independent of everything else).

### Phase 2: Macro Simplification

- Implement replay-stateless Macro Tactician with the decided confirmation rule.
- Run comparative backtests; enable the new policy only after it meets the pre-committed bar.
- Update CLAUDE.md Invariant J in the same change.

### Phase 3: User Configuration

- Add append-only tax-lot correction workflow with the decided precedence.
- Add versioned custom sleeves as named alternative configurations.
- Add validation and conflict-resolution views.

### Phase 4: Enforcement + Archive

- Remove persisted run-count regime transitions from authoritative decisions.
- Remove direct native-number financial calculations from authoritative frontend flows; add static checks.
- Add provider payload archive and provenance fields for the existing free sources (EDGAR, yfinance, FRED).
- Add invariant and replay tests to CI.

## 12. Success Metrics

- Zero output changes across repeated runs with identical dated inputs.
- Zero historical decision changes caused by provider revisions or taxonomy changes.
- Zero source-record mutations from tax-lot correction workflows.
- Zero known authoritative frontend financial calculations using native JavaScript numbers.
- Macro Tactician meets the existing pre-committed live-influence backtest bar before its tilt affects trade proposals.
- Users can resolve sleeve and lot-data issues without editing imported files or database rows directly.

## 13. Resolved Decisions

All open review questions have been answered by the owner. The decisions are folded into §5 and the functional requirements; this table is the record.

| # | Question | Decision |
|---|---|---|
| 1 | EDGAR mandatory, or normalized provider as primary? | EDGAR remains mandatory and primary (it is free and authoritative). Only free sources are eligible; other free sources may supplement. |
| 2 | Which normalized provider ships first? | None for now. Strictly free sources; the existing EDGAR/yfinance/FRED stack is wrapped behind the adapter contract. Paid providers out of scope. |
| 3 | Observation-date confirmation rule? | Tilt change requires its condition on ≥ 2 distinct observation dates within a rolling 10-trading-day window ending at `as_of`. |
| 4 | Circuit breaker scope and indicators? | Defensive-only. Triggers: credit spreads (FRED HY OAS) and VIX, thresholds in versioned regime policy. |
| 5 | Custom sleeves replace built-in taxonomy? | No. Named alternative configurations; built-in remains the default. |
| 6 | Authoritative decimal scale and rounding? | 4 decimal places, `ROUND_HALF_EVEN`. Display may round further but never feeds back. |
| 7 | Corporate actions required before tax-aware proposals trust user lots? | Splits, mergers, dividends, return-of-capital payments. Others degrade tax analysis. |
| 8 | Broker import vs. user lot correction conflict? | User correction wins by default; conflict flagged, broker value preserved. |

## 14. Implementation Plan (Handoff)

This is the sequenced execution plan. Each work package (WP) is independently mergeable and should land with its tests. Follow CLAUDE.md invariants throughout; where this PRD changes an invariant, the CLAUDE.md update is part of the same WP.

### WP-1: Trade Proposal Export (FR-6) — small, no dependencies

- Add `committee export-trades` CLI command (Typer) and a "Download Batch Trades" action in the dashboard Trades view.
- Implement Fidelity Batch Trade CSV and Schwab Order Import CSV writers in a new module under the rebalancer/output side (must not import `signals/` or `oracles/`).
- Pull broker account numbers from the `accounts` reference; warn (do not fail silently) when a required broker field is missing.
- Quantities and amounts serialize from `Decimal`; no float formatting.
- Tests: golden-file CSV outputs from synthetic proposals; missing-account-mapping warning path.

### WP-2: M8 Backtest Harness — prerequisite for WP-4

- Build the backtest harness from the existing M8 roadmap item: replay oracle/rebalancer runs over historical dated inputs.
- Must support computing, per policy variant: maximum drawdown, CAGR, switch count, turnover, response lag, implied tax drag (§10 metrics).
- Deterministic: same inputs → same backtest output. No live data fetches in CI.

### WP-3: Decimal Transport Contract (FR-5, backend half + frontend helpers)

- API serializes authoritative financial fields as decimal strings (audit existing FastAPI response models; switch numeric financial fields to string).
- Frontend: adopt one decimal library (recommend `decimal.js` or `big.js`), build centralized helpers (parse, arithmetic, compare, round, format currency/percent) with scale 4 and `ROUND_HALF_EVEN` matching backend `decimal` config.
- Add display-only chart adapters as the sole `Number` conversion point.
- Tests: AC-9 round-trip property tests; AC-10 adapter isolation.

### WP-4: Replay-Stateless Macro Tactician (FR-1)

- Accept explicit `as_of`; load only observations dated ≤ `as_of`.
- Replace two-run confirmation with the decided rule: condition on ≥ 2 distinct observation dates within a rolling 10-trading-day window.
- Keep asymmetric enter/exit bands, dead bands, and the defensive-only circuit breaker (HY OAS + VIX).
- Remove persisted run-count state from the decision path; regime result includes the full evidence set of FR-1.4 plus policy version.
- Run WP-2 backtests comparing old hysteresis vs. new policy; enable only after meeting the pre-committed bar (§12).
- **Update CLAUDE.md Invariant J in this WP** to describe observation-date confirmation instead of two-run confirmation.
- Tests: golden-file replay (neutral/defensive/aggressive/circuit-breaker), lookahead exclusion, AC-1, AC-2.

### WP-5: Tax-Lot Correction Workflow (FR-4)

- New `lot_corrections` table: append-only, `supersedes_id`, `validation_status` (active/conflicted/superseded/invalid), typed reason, audit fields.
- Lot derivation rebuilds deterministically from immutable sources + latest applicable corrections; user correction wins over broker import by default, conflicts flagged with broker value preserved.
- Corporate-action handling for splits, mergers, dividends, return-of-capital; degrade tax analysis otherwise.
- CLI + dashboard views for entering corrections and resolving conflicts; every decision writes audit rows (Invariant I).
- Tests: AC-7, AC-8; immutability tests proving no UPDATE/DELETE on source tables.

### WP-6: Custom Sleeves (FR-3)

- New tables: `sleeve_configs` (versioned), `sleeve_definitions`, `sleeve_assignments`.
- Named alternative configurations alongside the built-in taxonomy (default). Exactly-one-sleeve-per-instrument; reject configs that are unmapped, multiply mapped, or whose targets don't sum to 1 at scale 4.
- Oracle/scenario outputs reference the sleeve-config version; historical decisions render with their original taxonomy.
- Tests: AC-5, AC-6; household/account grain separation tests.

### WP-7: Provider Archive + Enforcement (FR-2 descoped, FR-5 enforcement)

- Add `provider_payloads` + `normalized_facts` tables; archive EDGAR/yfinance/FRED responses with hash, timestamps, parser version, normalization-policy version. New records on revision, never updates.
- Wrap existing free sources behind the adapter contract; deterministic precedence (EDGAR > supplementary free source) with visible conflicts.
- Frontend static check (ESLint rule or grep-based CI check) rejecting `parseFloat`/`Number()`/unary `+`/native arithmetic on financial fields outside display adapters.
- Add replay and invariant tests to CI.
- Tests: AC-3, AC-4.

### Sequencing and notes for the implementer

- Order: WP-1 → WP-2 → WP-3 → WP-4 → WP-5 → WP-6 → WP-7. WP-1 and WP-2 are independent and may proceed in parallel; WP-4 hard-depends on WP-2; WP-3 should precede WP-5/WP-6 so new UI uses decimal helpers from the start.
- Run `pytest` (full suite, currently ~289 tests) and `ruff` + `mypy` per WP; import-linter contracts must stay green (oracles ↛ rebalancer, rebalancer ↛ signals/oracles).
- All new tables are append-only where they record sources or decisions; no UPDATE/DELETE paths.
- All financial values `Decimal` end to end; scale 4, `ROUND_HALF_EVEN`.
- No LLM calls anywhere in these WPs (Invariant D).
