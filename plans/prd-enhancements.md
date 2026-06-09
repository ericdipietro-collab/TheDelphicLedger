# DelphiLedger Enhancements & Simplifications

**Status:** Draft for review  
**Owner:** TBD  
**Last updated:** 2026-06-09

## 1. Summary

This PRD defines a focused set of enhancements to simplify DelphiLedger's operation without weakening its auditability, determinism, financial precision, or portfolio-analysis model.

The proposed changes are:

1. Make the Macro Tactician reproducible without persisted run-count state.
2. Add normalized fundamentals providers behind a locally archived data-provider interface.
3. Add user-defined household analysis sleeves without mixing them with account placement.
4. Add a tax-lot correction workflow without mutating imported or derived records.
5. Enforce decimal-safe financial handling throughout the React frontend.

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
- Allow users to correct incomplete tax-lot data or correct errors.
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

The same observation history and policy version must always produce the same tilt, regardless of invocation count or prior application state.

### 5.2 Normalized Fundamentals Are an Adapter, Not a New Source of Truth

DelphiLedger may use a normalized fundamentals provider such as Tiingo or Alpha Vantage to simplify data ingestion. The provider must sit behind a common adapter interface.

Every provider response used by the system must be archived locally before normalization. Historical decisions must reference the archived payload and normalization-policy version used at decision time.

SEC EDGAR remains the preferred provenance source for supported US issuer facts. Normalized providers may act as:

- a convenience source;
- a fallback source;
- a source for unsupported instruments;
- a comparison source for data-quality checks.

The system must not silently replace previously used facts when a provider revises historical data.

### 5.3 Custom Sleeves Are Household Analysis Configuration

Custom sleeves define the household-level allocation taxonomy used by oracles, reporting, scenarios, and drift calculations.

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

### 5.5 Frontend Financial Values Remain Decimal-Safe

The API will serialize authoritative financial values as decimal strings. The frontend will construct decimal values directly from those strings.

Native JavaScript `number` values may be used only for non-authoritative presentation concerns, such as chart coordinates, after an explicit conversion at the visualization boundary.

No value converted to a JavaScript `number` may be sent back into the decision, trade, tax-lot, or persistence path as an authoritative financial value.

### 5.6 Trade Proposal Export (Batch CSV)

To reduce human error during execution, the system will export theoretical trade proposals into broker-compatible batch formats. This provides a bridge between the tool's deterministic output and the user's manual execution at their brokerage.

## 6. Functional Requirements

### FR-1: Replay-Stateless Macro Regime

1. A Macro Tactician run must accept an explicit `as_of` date.
2. The engine must load only observations available on or before `as_of`.
3. Confirmation rules must use distinct observation dates, not invocation count.
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

1. Fundamentals providers must implement a common adapter contract.
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
   - sum exactly to `1` under the configured decimal scale.
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
7. A later broker import must not silently overwrite an active user correction.
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
4. User-entered financial values must never be stored as native numbers.      
5. Form submissions must send normalized decimal strings.
6. Rounding mode and supported scale must be centrally configured and consistent with the backend.
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
| User correction conflicts with broker import | Preserve both, flag conflict, and require explicit resolution policy. |
| Correction quantity exceeds current account holding | Mark invalid or conflicted; do not silently apply. |    
| Wash-sale activity spans household accounts | Analyze replacement activity across all relevant household accounts while preserving account-level lots. |
| Corporate action changes basis | Require explicit supported action handling or degrade tax analysis. |        
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

### Phase 1: Foundations

- Add versioned policy/config identifiers to decision records.
- Add provider payload archive and provenance fields.
- Define decimal transport and frontend helper contracts.

### Phase 2: Macro and Provider Simplification

- Implement replay-stateless Macro Tactician.
- Add normalized-provider adapter and deterministic precedence policy.
- Run comparative backtests before enabling the new Macro policy by default.

### Phase 3: User Configuration

- Add versioned custom sleeves.
- Add append-only tax-lot correction workflow.
- Add validation and conflict-resolution views.

### Phase 4: Enforcement

- Remove persisted run-count regime transitions from authoritative decisions.
- Remove direct native-number financial calculations from authoritative frontend flows.
- Add invariant and replay tests to CI.

## 12. Success Metrics

- Zero output changes across repeated runs with identical dated inputs.
- Zero historical decision changes caused by provider revisions or taxonomy changes.
- Zero source-record mutations from tax-lot correction workflows.
- Zero known authoritative frontend financial calculations using native JavaScript numbers.
- Macro Tactician meets the existing pre-committed live-influence backtest bar before its tilt affects trade proposals.
- Users can resolve sleeve and lot-data issues without editing imported files or database rows directly.        

## 13. Open Review Questions

1. Should EDGAR remain mandatory for supported US equities, or may a normalized provider become the configured primary source?
2. Which normalized fundamentals provider should ship first, and what are its point-in-time and licensing guarantees?
3. What exact observation-date confirmation rule should replace the current two-run Macro Tactician rule?       
4. Should the circuit breaker be defensive-only, and which indicators may trigger it?
5. Should custom sleeves replace the built-in taxonomy or exist as named alternative configurations?
6. What decimal scale and rounding mode should be authoritative for money, quantities, prices, and weights?     
7. Which corporate actions must be supported before user-entered lots are considered reliable enough for tax-aware proposals?
8. When a broker import conflicts with a user lot correction, which source wins by default, if either?
