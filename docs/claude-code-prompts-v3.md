# Claude Code — Starter Prompts for The Delphic Ledger (v3)

Setup: create repo `delphic-ledger`, drop `delphic-ledger-design.md` in as `docs/DESIGN.md`,
and drop the earlier `signals.py` draft into `docs/reference/signals.py`. Run Prompt 0 once,
then one prompt per session/milestone. Keep sessions scoped to a milestone.

---

## Prompt 0 — CLAUDE.md (once, first)

> Read docs/DESIGN.md in full. Create CLAUDE.md capturing the non-negotiable invariants:
> (A) oracles emit (per_holding_scores, sleeve_targets, persona_constraints) — never trades;
> one shared rebalancer produces all trades; oracles/ must not import rebalancer/, and
> rebalancer/ must not import signals/ or oracles/ (enforce with import-linter in CI).
> (B) import_batches, position_snapshots, and transactions are immutable; holdings is always
> derived. (C) oracles analyze the household rollup; the rebalancer places per-account.
> (D) no LLM calls in any decision path; all decisions deterministic and reproducible.
> (E) all money/quantities are Decimal, never float. (F) no real portfolio data in the repo:
> data/ gitignored, examples synthetic only, no CI job fetches live data or publishes output,
> dashboard binds 127.0.0.1 only. (G) below-threshold instrument matches never auto-accept —
> they go to the unresolved queue. (H) no fabricated metrics: every displayed number is
> computed from a sourced input or rendered "n/a"; persona flavor text never enters scoring.
> (I) every mapping decision and every committee run writes its audit row.
> (J) decisions run on demand, but the macro/regime sleeve tilt carries hysteresis state
> (asymmetric bands + two-run confirmation + defensive circuit breaker); security-level
> oracle scores are stateless.
> Stack: Python 3.12, uv, SQLAlchemy+SQLite, Typer+Rich CLI, pytest, ruff, mypy strict on
> core; dashboard = FastAPI + React/Vite + Recharts, local-only. Style: small modules,
> pydantic at boundaries, no clever metaprogramming.

## Prompt 1 — M1: ingestion (positions + transactions + templates)

> Read docs/DESIGN.md §3–4. Scaffold the project (pyproject via uv, src/committee, Typer
> entry `committee`). Implement all §4 models. Build `committee import` for BOTH positions
> and transactions through one pipeline: hashed immutable raw batches, junk-row pre-clean,
> header mapping (synonym dict, then rapidfuzz token_sort_ratio with 90/70 thresholds),
> file-type auto-detection from mapped columns. Implement the mapping review UX as specified:
> show the full proposed column mapping with sample values, allow correction of any
> assignment, save as a named template keyed on broker fingerprint; templates auto-apply
> later with a one-screen confirmation, export/import as YAML. Implement transaction-type
> canonicalization with an unknown-type queue that persists alias rules per template. Ship
> synthetic Schwab/Fidelity/Vanguard fixtures in examples/ for BOTH file types, including
> nasty cases (footer disclaimers, Account Total rows, BRK.B vs BRK/B, SPAXX sweep, a fund
> with description but no ticker, a "Reinvest Shares" transaction type). Golden-file tests
> per template. Do not build the instrument resolver yet.

## Prompt 2 — M1b: instrument resolution + holdings

> Read DESIGN.md §3.4 and §4. Implement the resolver cascade exactly as ordered: exact
> ticker → normalized ticker → alias table (sweeps→cash) → name fuzzy (auto ≥93, queue
> 80–92, queue bare <80) → OpenFIGI with local caching → human confirmation via an
> interactive `committee resolve` (accept / search / create / mark-cash). Every step writes
> mapping_decisions. Add instrument classification (type, asset_class, sleeve; individual
> equities outside fund types are flagged for the unwind path). Derive holdings (household
> and per-account views) from latest snapshots and implement `committee holdings`.
> Property-test cascade ordering (higher-priority match always wins) and test that nothing
> below threshold ever auto-accepts.

## Prompt 3 — M2: market data + universe

> Read DESIGN.md §5. Implement the market data layer: EOD price/dividend adapter
> (Tiingo-or-yfinance behind one interface), FRED fetchers (T10Y3M, CPIAUCSL→YoY, DTWEXBGS,
> VIXCLS, BAMLH0A0HYM2), and an EDGAR client honoring the ~10 req/s courtesy limit with a
> proper User-Agent, caching companyfacts XBRL (revenue, EPS, equity, total debt, CFO,
> capex, dividends) into fundamentals, plus the submissions index with 8-K item-code flags
> (4.01, 4.02, 5.02, 2.06). All observations append-only into market_observations with
> point-in-time stamps. Per-source freshness SLAs, hard timeouts, exponential backoff,
> degraded flags — a broken feed must never thrash and stale data must never silently feed
> a decision. Build the universe as the BUNDLE system per DESIGN.md §5: bundles/*.yaml
> objects with explicit, index_proxy, and mixed member sources. Ship six bundles: etf_core
> (write a reasonable ~80-ETF starter list), sp500 (iShares IVV holdings CSV), dow30
> (SPDR DIA holdings CSV), nasdaq100 (Invesco QQQ holdings CSV — the full Nasdaq Composite
> is explicit opt-in only, never shipped enabled), russell2000 (iShares IWM holdings CSV,
> refresh on-demand only), and watchlist (user-owned watchlist.yaml, sleeve-tagged). Every
> index_proxy file ingests through the EXISTING import pipeline and tags instruments with
> bundle_tags. Implement `committee universe [enable|disable|refresh|status]`, per-bundle
> refresh policies, and the scrape guard: data loaders fetch only the active universe
> (holdings + watchlist + enabled bundles), enforced by a global instrument cap (default
> 3,000, configurable) that warns at 80% and refuses beyond it with a disable-a-bundle
> message. Integration tests use recorded fixtures, never live calls in CI.

## Prompt 4 — M3: reconciliation

> Read DESIGN.md §6. Implement the quantity-grain roll-forward projector: expected qty at
> T1 = qty at T0 + canonical transaction effects (buys/sells/splits via instrument_events/
> reinvests/transfers). A snapshot becomes a checkpoint only when transaction coverage
> spans the gap; otherwise mark coverage_gap. Run the auto-explanation pass before opening
> any break; apply the materiality tolerance (default 0.5 shares or 0.1% of qty,
> configurable) and auto-close immaterial deltas. Build `committee recon` to list, drill
> into, and resolve breaks with recorded resolutions. Test with doctored fixtures: a missed
> reinvestment, an unrecorded transfer, a split, and a coverage gap.

## Prompt 5 — M4: oracles + rebalancer + constraint profiles

> Read DESIGN.md §7–8 and docs/reference/signals.py. Implement metric computation with
> fund-aware applicability (equity fundamentals n/a for funds and vice versa; "n/a" is a
> first-class result, never a fake number). Implement the six oracle configs from the §7
> table — computable metrics drive scoring; flavor strings appear only in display/voice.
> Scoring: per-metric percentile/threshold scores in [-1,+1] vs. universe, weighted mean of
> applicable metrics, abstain below a minimum-applicability floor. Oracles emit
> (per_holding_scores+reasons, sleeve_targets, persona_constraints). Per DESIGN.md Invariant
> D, implement the regime state machine that wraps signals.py's composite/veto: asymmetric
> hysteresis enter/exit bands + two-run confirmation before a macro sleeve tilt changes, with
> a credit-spread/VIX circuit breaker permitted to tighten defensively without confirmation.
> Only the Macro Tactician's tilt carries this state; security-level scores stay stateless.
> Implement the shared
> rebalancer per §8: drift bands, new-money-first, household-analysis/per-account placement,
> tax-aware ordering, whole shares, min trade. Implement constraint profiles as YAML rule
> objects with `unconstrained` and `funds_only` shipped, applied inside the rebalancer
> only. Build `committee convene`, `committee dissent` (six-way side-by-side with
> disagreement and rivals' objections), `committee minutes`. Add the import-linter contract
> from CLAUDE.md and full decisions audit rows.

## Prompt 6 — M5: scenario packs

> Read DESIGN.md §9. Implement scenarios as frozen YAML packs (indicator overrides +
> per-sleeve price shocks). `committee scenario <pack>` revalues the portfolio
> arithmetically and re-runs all oracles on shocked inputs, fully deterministically —
> identical inputs must produce byte-identical decision records (test this). Author the six
> shipped packs (Black Monday 1987, Dot-com 2000–02, GFC 2008, COVID 2020, hypothetical
> AI-bubble burst, +300bps rate shock) with sleeve-level shock magnitudes drawn from the
> historical record, documented inline with sources and the survivorship-bias honesty note.

## Prompt 7 — M6: tax lots + unwind queue

> Read DESIGN.md §8 (tax) and the lots model. Implement lot ingestion where CSVs provide
> it and average-cost fallback (basis_quality=average_fallback) where they don't; degraded
> tax notes for fallback positions, never synthetic lots. ST/LT computation, days-to-LT,
> wash-sale 30-day detection. Build `committee unwind`: individual (non-fund) positions
> with lot-level gain/loss, estimated tax impact at configurable marginal rates, and a
> suggested ordering (losses and LT gains first; ST gains flagged with wait-days). All
> output framed as information, never directives. Also implement the deviation log
> (DESIGN.md §8): `committee deviate` records the user's chosen action vs. the committee's,
> a typed reason, and surfaces the running override history — writing to the deviations
> audit table. Personal-mode feature; keep it out of any showcase/synthetic path.

## Prompt 8 — M7: the dashboard (make it sparkle)

> Read DESIGN.md §10. Build `committee serve`: FastAPI read-only JSON API over the SQLite
> store + a React/Vite/Recharts front end, bound to 127.0.0.1 only. Views: The Chamber
> (six oracle cards, dissent matrix with disagreement highlighted, rivals' objections),
> Portfolio (household allocation donut, drift gauges vs. selected oracle, holdings table
> with per-oracle scores and 8-K flags), Trades (per-oracle proposals with rationale/tax
> notes and the constraint-profile toggle), Scenario theater (pack picker, before/after
> valuation waterfall, verdict deltas), Recon (checkpoint timeline, breaks, coverage gaps),
> and a config-knob panel (bands, min trade, tolerance, contribution — engine parameters
> only, never market inputs). The dashboard reads the same decision records the CLI writes.
> Aim for a polished, screenshot-worthy visual design.

## Prompt 9 — M8: backtest + public polish

> Read DESIGN.md §11 and §1. Build `committee backtest`: point-in-time replay (write a test
> that fails on lookahead), Passive Pragmatist as benchmark, metrics per §11, ±50% parameter
> perturbation report, and the pre-committed pass-bar from §11 (a regime tilt earns live
> influence only if it cuts max drawdown ≥20% relative while costing ≤~1% CAGR with
> single-digit switches/decade; otherwise the Macro Tactician falls back to no tilt and the
> persona is labeled entertainment-only in dissent). Then make the repo public-ready: README with the epigraph ("Know thy
> holdings."), the tagline (Six oracles. Zero consensus. No predictions.), architecture
> diagram, a full synthetic-data walkthrough, the "what this demonstrates" section, and the
> disclaimer. Write docs/ADR/0001-oracle-rebalancer-decoupling.md,
> 0002-immutable-snapshots-derived-holdings.md, 0003-quantity-grain-reconciliation.md,
> 0004-constraint-profiles-as-policy.md. Verify .gitignore covers data/, *.db, real
> constraint profiles, and watchlists. Capture the dashboard screenshot and demo GIF.

---

## Session habits

- Open every session: "Read CLAUDE.md and docs/DESIGN.md §<relevant> before changing anything."
- Close every milestone: full test suite + ruff + mypy green; summarize changes and any
  DESIGN.md deviations; commit and tag (v0.1 ingest … v0.8 public).
- When Claude Code proposes drifting from an invariant, point at CLAUDE.md rather than
  re-litigating.
