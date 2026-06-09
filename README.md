# Delphic Ledger

A personal portfolio analysis tool built as an engineering showcase. Runs entirely locally — no cloud accounts, no broker integrations, no trade execution. Not investment advice.

---

## What it does

Delphic Ledger imports brokerage export files (Schwab, Fidelity, Vanguard), then runs six independent scoring oracles against your holdings to surface conviction from multiple investment philosophies simultaneously. A shared rebalancer converts oracle output into trade proposals that respect drift bands, tax account placement, and your chosen constraint profile.

The dashboard lets you explore each oracle's take, compare disagreements across the panel, run scenario stress tests, and review reconciliation breaks — all without sending your data anywhere.

---

## The Six Oracles

Each oracle represents a distinct investment philosophy. They score independently and never share state.

| Oracle | Philosophy | Signals |
|---|---|---|
| **The Value Purist** | Margin of safety — only buys below intrinsic value | P/E, P/B, FCF yield, D/E |
| **The Growth Visionary** | Own category winners before consensus catches up | Revenue YoY, gross margin trend, 6-month momentum |
| **The Yield Harvester** | Income first — the check must clear | Distribution yield, payout ratio, dividend growth streak |
| **The Macro Tactician** | Right-size risk for the current regime | Yield curve, CPI, DXY, VIX, credit spreads |
| **The Quant** | Humans are biased; price signals are not | RSI(14), 50/200 MA cross, 12-1 momentum, beta vs SPY |
| **The Passive Pragmatist** | Minimise cost and concentration drag | Expense ratio, HHI, implied turnover |

The Macro Tactician is special: it reads macro signals to set sleeve-level allocation targets (equity, fixed income, alternatives) rather than scoring individual securities. Every other oracle scores holdings and proposes trades; the Tactician sets the weights the others must satisfy.

---

## Architecture

```
Import (CSV/XLSX)
    └─ Ledger engine  ─── Holdings (transaction roll-forward or snapshot)
                              │
                         6× Oracle runs
                         (score per holding, sleeve targets)
                              │
                         Shared Rebalancer
                         (drift detection → trade proposals)
                              │
                         FastAPI  ──  React Dashboard
```

**Key invariants (never violated):**

- **Oracles never emit trades.** They emit scores, sleeve targets, and persona constraints. One rebalancer turns that into proposals.
- **Source records are immutable.** Import batches, position snapshots, and transactions are never updated or deleted after write.
- **No LLM in any decision path.** All scoring is deterministic pure Python reproducible from the same inputs. The narration layer (oracle "voice" text) is off by default and never feeds back into scoring.
- **`Decimal` everywhere.** All monetary amounts and share quantities use `decimal.Decimal` — never `float`.
- **No fabricated metrics.** Every number is either computed from a sourced input or rendered as `n/a`. Stale data is flagged, not silently carried forward.

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Package manager | uv |
| ORM / DB | SQLAlchemy + SQLite (Postgres-ready via connection string) |
| CLI | Typer + Rich |
| Tests | pytest (280+ tests) |
| Linter / formatter | ruff |
| Type checking | mypy strict on core layers |
| Dashboard API | FastAPI (local-only, binds 127.0.0.1) |
| Dashboard UI | React + Vite + Tailwind + Recharts |
| Market data | Tiingo (API key) or yfinance (fallback, no key) |
| Macro data | FRED (St. Louis Fed public API) |
| Fundamentals | SEC EDGAR XBRL |

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — `pip install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Node.js 18+ (for the dashboard)

Optional but recommended:
- `TIINGO_API_KEY` env var — faster, more reliable price fetches than yfinance
- `FRED_API_KEY` env var — FRED macro data (free key from [fred.stlouisfed.org](https://fred.stlouisfed.org/docs/api/api_key.html))

---

## Setup

```bash
# Clone and install Python dependencies
git clone https://github.com/<you>/DelphiLedger.git
cd DelphiLedger
uv sync

# Install dashboard dependencies
cd dashboard && npm install && cd ..
```

---

## Usage

### Import your holdings

Export a positions/transactions CSV from your broker (Schwab, Fidelity, or Vanguard) and import:

```bash
uv run committee import path/to/positions.csv
uv run committee import path/to/transactions.csv   # optional — enables transaction roll-forward
```

### Fetch market data

```bash
uv run committee fetch-prices    # EOD prices (Tiingo or yfinance)
uv run committee fetch-macro     # FRED macro series (yield curve, CPI, VIX, etc.)
uv run committee fetch-edgar     # SEC fundamentals (P/E, FCF, revenue growth)
```

### Run the committee

```bash
uv run committee convene         # Run all six oracles, generate trade proposals
uv run committee dissent         # Show disagreements across oracles
```

### Start the dashboard

```bash
uv run committee serve           # Starts FastAPI on 127.0.0.1:8000
cd dashboard && npm run dev      # Vite dev server on localhost:5173
```

Open [http://localhost:5173](http://localhost:5173).

---

## Dashboard

The dashboard is organized around five views:

**Chamber** — The main view. Oracle cards show each oracle's convictions with scores, sleeve targets, and the macro regime. A dissent matrix highlights where oracles disagree. Bundle toggles let you expand the buy universe beyond your current holdings (ETF Core, Dow 30, Nasdaq Top 50, S&P 500 snapshot, Russell 2000 snapshot).

**Portfolio** — Holdings table with sleeve allocations, drift gauges, and per-oracle scores side by side for each position.

**Trades** — Proposed rebalancing trades from the latest convene, with rationale tags, oracle scores, and tax notes. Supports recomputing with custom drift bands, minimum trade size, and new-money deployment.

**Scenarios** — Stress test packs that apply predefined market shocks (equity drawdown, rate spike, credit spread blowout) and show how the committee's proposals change.

**Recon** — Reconciliation breaks between expected and actual positions, with suggested causes and coverage gap flags.

---

## Buy universe / bundle system

By default the rebalancer only proposes buys among your current holdings. To let it propose new positions, seed and enable instrument bundles from the Chamber dashboard:

1. Click **Seed bundles** — registers all bundle instruments in the database
2. Click the toggle pills to enable the bundle(s) you want (e.g. *etf core*)
3. Click **Fetch prices** — downloads EOD prices for the newly-activated universe
4. Click **Re-convene** — the rebalancer now considers universe instruments as buy candidates

Subsequent "Fetch prices" runs skip instruments with recent data (3-day freshness window), so re-fetching after enabling an additional bundle only downloads the new instruments.

Available bundles:

| Bundle | Contents |
|---|---|
| ETF Core | ~80 diversified ETFs (equity, fixed income, sectors, factors, international) |
| Dow 30 | 30 DJIA components |
| Nasdaq Top 50 | 50 largest Nasdaq-listed stocks |
| S&P 500 | Static snapshot of ~450 index components |
| Russell 2000 | Representative ~300 small-cap stocks |

---

## Configuration

Engine parameters are adjustable from the dashboard Config panel or via API:

| Parameter | Default | Description |
|---|---|---|
| `drift_abs` | 5% | Absolute sleeve drift before rebalancing triggers |
| `drift_rel` | 25% | Relative sleeve drift before rebalancing triggers |
| `min_trade_usd` | $200 | Minimum trade size (filters noise) |
| `new_money` | $0 | Fresh cash to deploy before any sells |

Constraint profiles (e.g. `funds_only`) can be passed at convene time to restrict the trade universe.

---

## Running tests

```bash
uv run pytest
```

280+ tests covering the oracle scoring pipeline, rebalancer engine, reconciliation, API routes, and scenario stress tests.

---

## Non-goals

This project intentionally does not:

- Host or serve multiple users
- Integrate with any broker API or execute trades
- Use any LLM in scoring, regime detection, or trade proposal logic
- Predict returns or publish market calls
- Run in CI against live market data

---

## Project status

| Milestone | Status |
|---|---|
| M1 — Schema, import, mapping, holdings | Complete |
| M2 — Market data, bundle system, FRED, EDGAR | Complete |
| M3 — Reconciliation engine | Complete |
| M4 — Oracles, rebalancer, constraint profiles | Complete |
| M5 — Scenario packs | Complete |
| M6 — Tax lots, unwind queue | Complete |
| M7 — Dashboard (FastAPI + React) | Complete |
| M8 — Backtest, docs, demo | In progress |

---

*Personal portfolio analysis and engineering showcase. Locally run. Not investment advice.*
