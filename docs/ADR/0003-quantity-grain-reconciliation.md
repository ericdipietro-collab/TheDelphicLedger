# ADR 0003: Quantity Grain for Reconciliation

**Status:** Accepted  
**Date:** 2024-01-01

## Context

Portfolio reconciliation compares two things: what the transaction ledger says you own (ledger-projected quantity) and what the broker confirms you own (position snapshot quantity). These must be compared at the same grain to be meaningful.

The question is: what is the right grain? Options include:
1. Position (instrument only, summed across accounts)
2. Account × instrument
3. Lot level (account × instrument × acquisition date)

## Decision

Reconciliation runs at **account × instrument** grain.

The recon engine:
1. Identifies the most recent position snapshot for each (account, instrument) pair.
2. Rolls forward from that snapshot using transactions in `[snapshot_date, target_date]`.
3. Compares the projected quantity to the latest snapshot quantity (if a newer snapshot exists).
4. Opens a `ReconBreak` row for any non-zero delta.

Lot-level detail is tracked separately in `lots/` for tax-lot analysis but is not the reconciliation grain. This is a deliberate simplification: lot-level recon would require exact FIFO or specific identification tracking that most broker exports don't cleanly provide.

## Consequences

**Good:**
- Account-grain breaks surface the most actionable problems: "my taxable account shows 97 shares but the ledger says 100."
- Lot-level gaps (unknown cost basis, average-fallback lots) are reported separately via `basis_quality` flags, not as quantity breaks.
- Transfers between accounts show as a break at each account (buy at destination, sell at source) — this is intentional and surfaces inter-account activity for review.

**Trade-off:**
- A lot-for-lot exact match is not attempted. Wash-sale tracking is advisory only.
- Intra-account transfers (e.g. between tax-advantaged and taxable) show as two breaks until the transfer transaction is recorded.
- Coverage gaps (no snapshot, no transaction in a long window) are flagged separately as `coverage_gap=True` to distinguish them from real discrepancies.
