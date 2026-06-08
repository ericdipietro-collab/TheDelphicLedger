# The Delphic Ledger
### Design Document — v3 (supersedes investment-committee-design v2)

> **"Know thy holdings."** — after the inscription at the Temple of Apollo at Delphi

Six oracles. Zero consensus. No predictions.

A personal portfolio analysis tool and engineering showcase: import real positions and transactions from messy broker CSVs, reconcile them, enrich them with market data and SEC filings, then convene six investor archetypes — the oracles — who score the portfolio through incompatible philosophies and propose conflicting rebalancing trades. Entertainment built on real-world mechanics, demonstrating a capital-markets data-architecture skillset.

**Repo:** `delphic-ledger` · **Package/CLI:** `committee` · **License:** MIT
**Owner:** Eric DiPietro · **Status:** Build-ready

---

## 1. Identity, scope, and perimeter

**What it is:** a locally run decision-support and simulation tool. The user imports their own data; the oracles produce *theoretical* trades; a human decides what, if anything, to do.

**Non-goals (load-bearing — enforced in CLAUDE.md):**
1. **No hosting, no multi-user service.** Public repo distributes software; the author never operates it for others.
2. **No published market calls.** Repo contains code + synthetic examples only. Real data and real outputs are local and gitignored. No CI job fetches live data or publishes generated output.
3. **No broker API, no execution.** Output is a proposed-trade report.
4. **No LLM in any decision path.** All scoring, regime, and trade logic is deterministic pure Python, reproducible from inputs. Optional LLM narration (off by default) describes already-made decisions.
5. **No return prediction.** Oracles apply pre-committed philosophies to observable data. Scenario analysis replays *hardcoded* historical/hypothetical shocks; it never simulates market dynamics.
6. **No fabricated metrics.** Every number shown is computed from a sourced input or explicitly absent ("n/a"). Persona flavor language never masquerades as data.

**README carries:** prominent not-investment-advice / entertainment-and-education disclaimer; the epigraph; a "what this demonstrates" section (entity resolution with human-in-the-loop conformance, event-sourced reconciliation, constraint-based portfolio logic, deterministic what-if replay, point-in-time data discipline).

---

## 2. System overview

```
 broker CSVs (positions, transactions)
        │
        ▼
 ┌──────────────────────────────────────────────┐
 │ INGEST  raw batches (immutable, hashed)      │
 │  header mapper ←→ user-corrected templates   │
 │  instrument resolver → mapping_decisions     │
 │  unresolved queue → human confirm            │
 └──────────────┬───────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────┐
 │ CORE  instruments (security master)          │
 │       position_snapshots · transactions      │
 │       lots · holdings (derived, household)   │
 │       recon_breaks                           │
 └──────┬──────────────────────────┬────────────┘
        ▼                          ▼
 ┌──────────────────┐   ┌─────────────────────────┐
 │ MARKET DATA      │   │ RECON ENGINE            │
 │ prices · FRED    │   │ qty roll-forward between│
 │ EDGAR XBRL/8-K   │   │ snapshots vs txn ledger │
 │ universe loader  │   └─────────────────────────┘
 └──────┬───────────┘
        ▼
 ┌──────────────────────────────────────────────┐
 │ ORACLES (6 persona configs)                  │
 │ each: per-holding scores + sleeve targets    │
 │       + persona constraints                  │
 └──────────────┬───────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────┐
 │ REBALANCER (single, shared)                  │
 │ f(scores, targets, holdings, lots,           │
 │   constraint profile) → TradeProposal        │
 └──────────────┬───────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────┐
 │ OUTPUT  CLI (committee …) · local web        │
 │ dashboard · decision audit log · scenarios   │
 └──────────────────────────────────────────────┘
```

**Invariant A (decoupling, enriched):** an oracle emits `(per_holding_scores, sleeve_targets, persona_constraints)` — never trades. One shared rebalancer produces all trades. `oracles/` must not import `rebalancer/`; `rebalancer/` must not import `signals/` or `oracles/`. Enforced with an import-linter contract in CI.

**Invariant B (immutability):** raw import batches, snapshots, and transactions are immutable. `holdings` is always derived (ledger-projected where transaction coverage exists; latest-snapshot otherwise).

**Invariant C (analysis grain):** oracles analyze the **household rollup** (all accounts combined); the rebalancer places trades **per account**, tax-aware.

**Invariant D (cadence & smoothing):** decisions run **on demand** (the user invokes `convene`/`dissent`), but any regime-driven sleeve tilt — chiefly the Macro Tactician's — passes through **hysteresis** so repeated runs don't flip-flop on noise: asymmetric enter/exit bands plus a two-run confirmation before a tilt changes, with a credit-spread/VIX circuit breaker allowed to tighten defensively without waiting for confirmation. (The composite and veto logic already exist in `signals.py`; the regime state machine wraps them.) Security-level oracle scores are stateless; only the macro/regime tilt carries memory.

---

## 3. Ingestion (Acceptance Criteria 1–2)

Same pipeline serves positions and transactions; only the canonical target schema differs.

1. **Intake:** `committee import <file> --account X [--type positions|transactions]` (type auto-detected from mapped columns, overridable). Raw bytes + SHA-256 stored in `import_batches`; duplicate hash = no-op. Pre-clean strips footer disclaimers, "Account Total" rows, blank lines, currency symbols, parenthesized negatives.
2. **Header mapping:** synonym dictionary first; rapidfuzz token_sort_ratio fallback (≥90 auto, 70–89 prompt, <70 unmapped+warn).
3. **Mapping review UX (explicit AC):** after auto-mapping, the user is shown the full proposed mapping (source column → canonical field, with sample values), can correct any assignment, and **saves the result as a named template** (e.g. "Schwab positions"). Templates key on broker fingerprint (header-set hash) and auto-apply on future imports, still showing a one-screen confirmation. Templates are exportable YAML; Schwab/Fidelity/Vanguard starter templates ship in `profiles/` and double as test fixtures.
4. **Instrument resolution cascade** (strictly ordered, every step audited to `mapping_decisions`): exact ticker → normalized ticker (`BRK.B`≡`BRK/B`≡`BRK B`) → alias table (money-market sweeps → cash) → name fuzzy vs. security master (≥93 auto, 80–92 queue-with-candidates, <80 queue-bare) → OpenFIGI lookup (cached into `instruments`) → human confirm via `committee resolve`. **Nothing below auto-threshold is ever silently accepted.**
5. **Transaction canonicalization:** types normalized to {buy, sell, dividend, interest, fee, transfer_in, transfer_out, split, reinvest}; unknown types queue for classification and persist as alias rules per template.

---

## 4. Data model

| Table | Nature | Notes |
|---|---|---|
| `instruments` | security master | ticker, FIGI, name, type (stock/etf/mutual_fund/cash), asset_class, sleeve, aliases[], bundle_tags[] |
| `accounts` | reference | broker, owner, tax_type (taxable/trad/roth/401k) |
| `import_batches` | immutable | file hash, raw bytes, template_id, type |
| `mapping_templates` | user-owned | broker fingerprint → column map + type aliases |
| `mapping_decisions` | audit | raw value, candidate, score, method, accepted_by |
| `position_snapshots` | immutable | per batch: instrument, qty, cost basis, market value, as_of |
| `transactions` | immutable ledger | canonical type, qty, amount, trade_date |
| `lots` | tax lots | qty, basis, acquired_at; `basis_quality` = lot \| average_fallback |
| `holdings` | derived | household + per-account views; source = ledger \| snapshot |
| `recon_breaks` | derived | see §6 |
| `market_observations` | append-only | prices, FRED series, derived indicators (point-in-time stamped) |
| `fundamentals` | cached EDGAR | XBRL facts per instrument per period; filing index + 8-K item codes |
| `decisions` | audit | run id, full input vector, all oracle scores, proposals, scenario id if any |
| `deviations` | audit | personal-mode: when the user overrides the committee — chosen action, committee's action, typed reason, timestamp |

SQLite via SQLAlchemy (Postgres = connection-string swap). Money/quantities as Decimal. `data/` gitignored.

---

## 5. Market data & universe (AC 4–5)

**Sources (all free):**
- **Prices/dividends:** Tiingo or yfinance adapter, EOD only (no intraday anywhere — deliberate simplification). Nightly batch for the active universe.
- **Macro (FRED):** T10Y3M, CPIAUCSL (YoY derived), DTWEXBGS (dollar index), VIXCLS, BAMLH0A0HYM2.
- **EDGAR:** companyfacts XBRL (revenue, EPS, equity, debt, CFO, capex, dividends) and submissions index (8-K item codes 4.01/4.02/5.02/2.06 surfaced as event flags on holdings). Respect the ~10 req/s courtesy limit; nightly batch; full-universe initial load ≈ minutes. *(Deferred future module — "the bug in the Edgar suit": narrative filing-diffs (risk-factor changes, new going-concern language) and NT/late-filing flags as an additional signal. Out of scope for v1; the `fundamentals`/filing index schema leaves room for it.)*
- **Fund data:** expense ratio and category via yfinance where available; absent values render "n/a", never invented.

**Universe = bundles (buy-candidate space, deliberately bounded):**

A **bundle** is a first-class, named universe object (`bundles/*.yaml`): id, description, member source, refresh policy, and size. Member sources come in three kinds:
- `explicit` — a literal ticker list (named companies, curated ETF sets);
- `index_proxy` — an ETF issuer's free daily holdings CSV, ingested through the **same import pipeline** (dogfooding) and tagged;
- `mixed` — composition of the above.

**Shipped bundles:**

| Bundle | Source | ~Size | Notes |
|---|---|---|---|
| `etf_core` | explicit (`~80 liquid ETFs across sleeves`) | 80 | the funds_only default |
| `sp500` | index_proxy: iShares IVV holdings CSV | 500 | S&P 500 |
| `dow30` | index_proxy: SPDR DIA holdings CSV | 30 | Dow Jones Industrial Average |
| `nasdaq100` | index_proxy: Invesco QQQ holdings CSV | 100 | **default "Nasdaq"; full Composite (~3,400) is explicit opt-in only** |
| `russell2000` | index_proxy: iShares IWM holdings CSV | ~2,000 | the heavyweight; refresh on demand, not nightly |
| `watchlist` | explicit, user-owned (`watchlist.yaml`) | user | sleeve-tagged named companies |

(Index constituent lists aren't freely licensed; issuer ETF holdings files are the standard free proxies and the README says so plainly.)

**Active universe** = household holdings (always in) + watchlist + the bundles the user has enabled (`committee universe enable sp500`). **Scrape guard:** data loaders fetch only for the active universe, and a global instrument cap (default 3,000, configurable) warns at 80% and refuses beyond it with a "disable a bundle" message — preventing anyone from pointing the EDGAR/price loaders at the entire market by accident. Per-bundle refresh policies (nightly for small bundles, on-demand for russell2000) keep the batch budget sane against EDGAR's courtesy limit.

Oracles rank within the active universe; per-oracle filters then apply (e.g., Yield Harvester ignores zero-yield names; constraint profiles can pin a persona run to `etf_core`).

**Staleness posture:** every source has a freshness SLA; stale inputs flag affected metrics degraded; degraded metrics drop out of scoring with weight redistribution; a fully degraded oracle reports "abstain" rather than guessing. Hard timeouts + exponential backoff; a broken feed never thrashes.

---

## 6. Reconciliation (AC 3) — core scope

**Grain: security × quantity** (not market-value pennies — explicit decision).

- Roll-forward projector: for each instrument, qty at snapshot T₀ + Σ canonical transaction quantity effects (buys, sells, splits via `instrument_events`, reinvests, transfers) ⇒ expected qty at T₁.
- A new snapshot becomes a **checkpoint** only if transaction coverage spans (T₀, T₁]; otherwise it's marked `coverage_gap`, not failed.
- **Auto-explanation pass before any break opens:** match dividends/reinvests/fees/splits first.
- **Materiality tolerance:** default 0.5 shares or 0.1% of position qty (configurable); inside tolerance ⇒ auto-closed as immaterial.
- Breaks open with suggested causes (missed reinvest, transfer, split, import gap); `committee recon` lists, drills, and resolves them; resolutions are recorded (audit).

---

## 7. The Oracles (AC 6)

Persona = YAML config: identity, philosophy text, **rivals** (for dissent theater), block weights, metric set, universe filter, sleeve targets, persona constraints, voice prompt (narration only). **Naming rule:** archetypes; factual lineage attribution allowed; never a real living person's name over generated words.

**Computable vs. flavor (Invariant: flavor never enters math):**

| Oracle | Computable metrics (source) | Flavor (voice only) |
|---|---|---|
| **The Value Purist** | P/E (price+EDGAR EPS) · P/B (EDGAR equity) · FCF yield (EDGAR CFO−capex / mkt cap) · Debt/Equity (EDGAR) | "moats", "margin of safety" |
| **The Growth Visionary** | Revenue YoY (EDGAR) · Gross margin & trend (EDGAR) · 6-mo price momentum | TAM, CAC, "category winner" |
| **The Yield Harvester** | Trailing dividend yield (price history) · Payout ratio (divs/EPS, EDGAR) · Consecutive years of increases (dividend history) · Fund distribution yield | "the check clears" |
| **The Macro Tactician** | T10Y3M · CPI YoY · DXY level/trend · VIX regime (all FRED; portfolio-level, drives sleeve tilts) | geopolitics narrative |
| **The Quant** | RSI(14) · 50/200 MA cross · 12-1 momentum · Beta vs. SPY (all price-derived) | "humans are biased" |
| **The Passive Pragmatist** | Expense ratios · Portfolio concentration (HHI) · Individual-stock share of portfolio · Turnover implied by others' proposals | the weary sigh |

Fund-aware applicability: equity-fundamental metrics return "n/a" for funds; fund analogues (expense ratio, distribution yield) return "n/a" for stocks. The Pragmatist is fully fund-native by design. Scoring: each metric → percentile/threshold score in [-1,+1] vs. universe; oracle score per holding = weighted mean of *applicable* metrics; abstain if too few apply.

Each oracle emits: per-holding scores (+top reasons), sleeve targets, constraints (e.g., Harvester: min yield; Visionary: max positions = concentration; Pragmatist: individual-stock target = 0).

---

## 8. Rebalancer & constraint profiles (AC 8 + the toggle)

Single engine: `propose(oracle_output, holdings, lots, accounts, constraint_profile, params) → TradeProposal`.

- Sells from low-scored / over-target; buys into high-scored / under-target within the oracle's universe; 5/25 drift bands; new-money-first routing; whole shares; min trade $200.
- **Household analysis, per-account placement:** tax-advantaged sells first; taxable sells flag ST lots and days-to-LT; wash-sale 30-day window on loss-harvest suggestions; `basis_quality=average_fallback` positions get degraded tax notes, never fake lot math.
- **Constraint profiles (the ETF/stock toggle, generalized):**
  - `unconstrained` — showcase mode; oracles may propose individual stock buys.
  - `funds_only` — no individual-security purchases; individual holdings are sell/hold only (flagged as the unwind queue with tax context).
  - Profiles are YAML rule objects (allowed instrument types, blocked lists, preclearance-required flags) — *modeling a trading policy as a first-class portfolio constraint*, exactly as enterprise compliance engines do. Personal profile mirrors the Workiva policy; it ships as an `examples/` template with the real one local-only.
- Output: per-account ordered trades with rationale tags (score, drift, constraint, unwind) and tax notes. Every run writes a full `decisions` audit row.
- **Deviation log (personal mode):** when the user chooses to override what the committee proposed, `committee deviate` records the chosen action, the committee's action, and a *typed reason*, then surfaces the running history of past overrides and how they fared. Cheap to build, and the single highest-value discipline feature for real personal use — the tool's quiet defense against your own impulses.

---

## 9. Scenario packs (AC 9, pulled forward as a deterministic feature)

No free-form market tweaking (cut deliberately). Instead, **hardcoded scenario packs**: frozen, versioned input vectors in `scenarios/*.yaml` — each defines indicator overrides (yield curve, VIX, CPI, spreads) and per-sleeve/asset-class price shocks. Applying a scenario (a) revalues the portfolio arithmetically and (b) re-runs all six oracles on the shocked inputs. Fully deterministic and replayable.

Shipped packs: Black Monday 1987 · Dot-com 2000–02 · GFC 2008 · COVID March 2020 · hypothetical AI-bubble burst · +300bps rate shock. (Honesty note in each pack: sleeve-level shocks, not security-level — replaying 2008 per-security invites survivorship bias; the README says so.)

`committee scenario gfc-2008` and a dashboard scenario picker.

---

## 10. Interfaces

**CLI (the engine of record):**
```
committee import <file> --account X      committee resolve
committee templates [list|show|edit]     committee holdings [--household|--account X]
committee recon                          committee universe [enable|disable|refresh|status]
committee convene [--oracle value_purist] [--profile funds_only]
committee dissent                        committee minutes [--last N]
committee scenario <pack>                committee unwind
committee deviate                        # personal mode: log an override + reason
committee serve                          # launches local dashboard
```

**Local web dashboard (`committee serve`, 127.0.0.1 only — the sparkle):**
FastAPI read-only JSON API over the SQLite store + React/Vite + Recharts front end. Views:
- **The Chamber:** six oracle cards, scores, and the dissent matrix — disagreement highlighted; rivals' objections rendered ("The Value Purist objects to the Visionary's proposal on 3 holdings").
- **Portfolio:** household allocation donut, drift gauges vs. selected oracle targets, holdings table with per-oracle score columns and 8-K event flags.
- **Trades:** proposal list per oracle/profile with rationale and tax notes; constraint-profile toggle.
- **Scenario theater:** pack picker; before/after valuation waterfall; oracle verdict deltas.
- **Recon:** checkpoint timeline, open breaks, coverage gaps.
- **Config knob sliders:** bands, min trade, materiality tolerance, contribution amount, profile selection. (Sliders adjust *engine parameters*, never market inputs.)
Charts re-query the same decision records the CLI writes — one engine, two skins. Nothing is hosted or published.

---

## 11. Backtest & honesty

Point-in-time replay from `market_observations` (a decision sees only data stamped ≤ decision date; a lookahead test must fail if violated). Benchmark: The Passive Pragmatist. Metrics: CAGR, max drawdown, Ulcer index, turnover and implied tax drag, switch/whipsaw counts. **Pre-committed pass-bar (stated before building, README-worthy as falsifiable rigor):** a tactical/regime tilt earns live influence only if it cuts max drawdown ≥20% relative to the Passive Pragmatist while costing ≤~1% CAGR, with single-digit regime switches per decade; tilts that miss the bar remain available in `dissent` as entertainment but are labeled, and the Macro Tactician falls back to no tilt. **Shadow stance:** for real personal use, run the committee for the first few months without acting on it and grade it against what you'd have done anyway before trusting any tilt. Honest-limitations appendix retained from v2: tactical value is episodic; parameter humility (±50% perturbation report); the tool's real edge is discipline, drift control, tax-lot hygiene, and the audit trail — not prophecy. The oracles are entertainment wrapped around that discipline.

---

## 12. Build order

| M | Scope | Demo at end of milestone |
|---|---|---|
| M1 | Schema, import (positions+transactions), mapping review + templates, resolver, holdings | import 3 brokers' synthetic CSVs, correct a mapping, save template |
| M2 | Market data layer: prices, FRED, EDGAR cache, bundle system (etf_core, sp500, dow30, nasdaq100, russell2000, watchlist), scrape guard | enable sp500, refresh, fundamentals visible on holdings |
| M3 | Recon: projector, checkpoints, tolerance, auto-explain | break opens on a doctored fixture; resolve it |
| M4 | Oracles + scoring + rebalancer + constraint profiles + convene/dissent/minutes | six conflicting trade lists on the same portfolio |
| M5 | Scenario packs | `committee scenario gfc-2008` |
| M6 | Tax lots, average fallback, unwind queue | `committee unwind` with ST/LT context |
| M7 | Dashboard (serve): Chamber, Portfolio, Trades, Scenario theater, Recon, knobs | the screenshot that tops the README |
| M8 | Backtest harness + polish: ADRs, README, demo GIF, synthetic walkthrough | repo public-ready |
