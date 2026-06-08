# ADR 0002: Immutable Source Records, Derived Holdings

**Status:** Accepted  
**Date:** 2024-01-01

## Context

Portfolio data has two kinds of mistakes: import errors (wrong CSV, misread header) and genuine corrections (broker amendment, data feed restatement). A system that allows UPDATE/DELETE on raw records makes it impossible to distinguish "we fixed a bug" from "we edited history."

Additionally, the `holdings` table needs to reflect the best available view at any moment: transaction roll-forward where coverage exists, snapshot fallback otherwise. If holdings are hand-edited, derivation logic and raw records diverge silently.

## Decision

**Source records are never mutated after write:**

- `import_batches` — append-only; duplicate hash = no-op
- `position_snapshots` — append-only; corrections append with a superseded flag
- `transactions` — append-only; corrections append a new row

No `UPDATE` or `DELETE` ever runs on these tables. This is a code-level invariant, not just a policy.

**Holdings are always derived**, never hand-edited:

- Where transaction coverage exists: ledger projection (roll forward from last snapshot using transactions through target date)
- Where it doesn't: latest snapshot for that instrument × account
- The `holdings` table is rebuilt by `committee recon run`; it is never a source of truth

## Consequences

**Good:**
- Full audit trail: you can always see what data was present at any point in time.
- Corrections are visible (the superseded record remains; the correction row shows the change).
- Holdings derivation is deterministic given the same source records; re-running produces the same result.
- Recon breaks (ledger says X, snapshot says Y) surface data gaps rather than hiding them.

**Trade-off:**
- The `holdings` table can drift stale if `committee recon run` isn't executed after new imports.
- Derived holdings require price data to compute market value; positions without price observations show `market_value = None`.
- Reporting "what did I own on 2022-06-15" requires replaying transactions from the nearest prior snapshot to that date, not a simple SELECT.
