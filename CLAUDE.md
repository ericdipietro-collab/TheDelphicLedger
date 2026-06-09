# Delphic Ledger — CLAUDE.md

Personal portfolio analysis and engineering showcase. Locally run. Not investment advice.
See `docs/delphic-ledger-design.md` for the full design.

---

## Architecture invariants — never violate these

### A. Oracle/rebalancer decoupling

Oracles emit exactly three things: `per_holding_scores`, `sleeve_targets`, `persona_constraints`.
**Oracles never emit trades.** One shared rebalancer (`rebalancer/`) consumes oracle output and produces all `TradeProposal` objects.

Import discipline — enforced by import-linter in CI:
- `oracles/` must not import anything from `rebalancer/`
- `rebalancer/` must not import anything from `signals/` or `oracles/`

If you need shared types between layers, put them in a `core/` or `models/` module that neither layer owns.

### B. Immutability of source records

`import_batches`, `position_snapshots`, and `transactions` are **never mutated after write**. No UPDATE or DELETE on these tables — ever. Corrections happen by appending a new record (with a superseded flag) or via audit rows.

`holdings` is always **derived**: ledger-projected (transaction roll-forward) where transaction coverage exists; latest snapshot otherwise. Never hand-edit `holdings` rows.

### C. Analysis grain vs. placement grain

Oracles score and analyze the **household rollup** — all accounts combined into one view.
The rebalancer places trades **per account**, tax-aware. These are different grains; don't conflate them.

### D. No LLM in any decision path

All scoring, regime detection, rebalancing, and trade proposal logic is deterministic pure Python, reproducible from the same inputs. LLM narration (generating oracle "voice" text) is a separate, clearly-labelled presentation layer that is off by default and never feeds back into scoring.

If you are writing code that influences a score, a trade, a drift band, or a regime state: **no LLM calls**. Period.

### E. Decimal for money and quantities

All monetary amounts and share quantities use Python `decimal.Decimal`. **Never `float`** for these values. This applies to prices, quantities, lot sizes, portfolio weights, trade amounts — everything financial. Intermediate calculations stay Decimal throughout.

```python
# correct
from decimal import Decimal
qty = Decimal("100")
price = Decimal("142.50")

# wrong — never do this
qty = 100.0
price = 142.50
```

### F. No real portfolio data in the repo

- `data/` is gitignored — verify `.gitignore` before every new directory under `data/`
- All examples and fixtures are **synthetic only**; they ship in `examples/` or `profiles/`
- No CI job fetches live market data or publishes generated output
- The dashboard (`committee serve`) binds `127.0.0.1` only — never `0.0.0.0`
- The constraint profile for personal use lives locally, outside the repo; only an `examples/` template ships

### G. Unresolved queue — no silent auto-acceptance

Instrument resolution has three outcome buckets:
- **≥ threshold** → auto-accept and log to `mapping_decisions`
- **Below threshold with candidates** → queue with candidates for `committee resolve`
- **Below threshold, no candidates** → queue bare for `committee resolve`

**Nothing below the auto-threshold is ever silently accepted.** Do not add a code path that promotes a below-threshold match to accepted without a human confirmation record in `mapping_decisions`. The same applies to header fuzzy-matching: scores 70–89 prompt the user; scores below 70 are unmapped and warned.

### H. No fabricated metrics

Every number displayed in the CLI or dashboard is either:
1. Computed from a sourced input (price data, EDGAR XBRL, FRED), or
2. Explicitly absent: render `"n/a"` — never invent a value, impute silently, or carry forward stale data without a staleness flag

Persona flavor text (`"moats"`, `"the check clears"`, geopolitical narrative) exists only in voice/narration output. **It never influences a score, a weight, or a trade.** The scoring path is metrics → percentile/threshold → weighted mean, with no text-derived inputs.

Fund metrics (`expense_ratio`, `distribution_yield`) return `"n/a"` for stocks. Equity fundamentals (P/E, P/B, FCF yield) return `"n/a"` for funds. The Pragmatist abstains from equity-fundamental scoring by design.

If a data source is stale beyond its freshness SLA, affected metrics are marked degraded. Degraded metrics drop from scoring with weight redistribution. A fully degraded oracle reports `"abstain"` — never guesses.

### I. Audit rows for every decision

Every instrument mapping decision → row in `mapping_decisions` (raw value, candidate, score, method, `accepted_by`).

Every `committee convene` / `committee dissent` run → row in `decisions` (run id, full input vector, all oracle scores, all proposals, scenario id if applicable).

Every user override → row in `deviations` (chosen action, committee's action, typed reason, timestamp).

Do not skip audit writes for "dry run" modes — write them with a `dry_run=True` flag or a separate record kind, but the write must happen.

### J. Observation-date confirmation for macro regime; stateless security scores

Security-level oracle scores are **stateless** — the same inputs always produce the same score. No memory between runs for individual security scores.

The macro/regime sleeve tilt (Macro Tactician) uses **observation-date confirmation**:
- A tilt change requires the entry (or exit) condition on at least two distinct observation dates within a rolling 10-trading-day window (≈14 calendar days) ending at `as_of`.
- Two observations sharing a date count as one confirmation date.
- Asymmetric enter/exit bands are preserved (enter defensive: < −0.30; exit: > −0.10).
- Defensive-only circuit breaker (HY OAS credit spreads / VIX) may trigger immediately without date confirmation.
- The same observation history, `as_of` date, and policy version always produce the same tilt, regardless of invocation count.

The persisted `RegimeState` table stores the last computed tilt for live-UI continuity. It is NOT used for confirmation decisions — confirmation is derived from archived market observations at each call.

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Package manager | uv |
| ORM / DB | SQLAlchemy + SQLite (Postgres = connection-string swap) |
| CLI | Typer + Rich |
| Tests | pytest |
| Linter | ruff |
| Types | mypy strict on `core/`, `oracles/`, `rebalancer/`, `signals/` |
| Import enforcement | import-linter (CI) |
| Dashboard API | FastAPI (read-only, local-only) |
| Dashboard UI | React + Vite + Recharts |

---

## Code style

- Small, focused modules — one clear responsibility per file
- Pydantic models at layer boundaries (ingest → core, oracle output, trade proposals, API responses)
- No clever metaprogramming — explicit beats magic
- No comments that describe *what* code does; only comments explaining *why* when the reason is non-obvious
- Prefer `match` statements over long `elif` chains for exhaustive enum dispatch

---

## Non-goals (enforced)

1. No hosting, no multi-user service
2. No published market calls — code + synthetic examples only
3. No broker API integration, no trade execution
4. No LLM in any decision path (narration layer only, off by default)
5. No return prediction — oracles apply philosophies to observable data; scenario packs replay hardcoded shocks
6. No fabricated metrics (see Invariant H)

---

## Build order

M1 → schema, import, mapping review, resolver, holdings  
M2 → market data, bundle system, FRED, EDGAR cache  
M3 → reconciliation engine  
M4 → oracles, rebalancer, constraint profiles, convene/dissent  
M5 → scenario packs  
M6 → tax lots, unwind queue  
M7 → dashboard  
M8 → backtest, README, demo  
