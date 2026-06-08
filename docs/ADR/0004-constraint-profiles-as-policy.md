# ADR 0004: Constraint Profiles as Declarative Policy

**Status:** Accepted  
**Date:** 2024-01-01

## Context

Different portfolio contexts have different hard rules: a tax-advantaged account might allow individual stocks the taxable account can't hold; a charitable fund might need to exclude certain sectors; a personal preference might require a minimum yield floor. These rules are not oracle preferences — they are hard constraints on what can be proposed.

The question is where these constraints live and how they interact with oracle scoring.

## Decision

Constraint profiles are **declarative YAML policies** consumed by the rebalancer, not the oracles.

```yaml
# profiles/funds_only.yaml
id: funds_only
description: ETFs and mutual funds only
allowed_instrument_types:
  - etf
  - mutual_fund
min_yield: null
max_positions: null
individual_stock_target: null
```

Profiles are loaded at rebalance time: `propose(oracle_output, session, constraint_profile=profile)`. The rebalancer applies the profile's rules as hard filters before generating `TradeProposal` objects.

Oracles score everything in their universe. The profile narrows what the rebalancer can act on. An oracle might score AAPL highly; under a `funds_only` profile, no AAPL trade proposal is generated.

Personal constraint profiles (with real ticker watchlists, account-specific rules, etc.) live outside the repo in `profiles/personal*.yaml`, which is gitignored. Only example templates ship in the repo.

## Consequences

**Good:**
- Oracle philosophies are pure: they score without knowing what's "allowed." The same Value Purist config works in any constraint context.
- Adding a new constraint dimension (e.g. ESG exclusion list) requires only a new profile key and rebalancer logic — no oracle changes.
- Constraint decisions are audited: the `inputs_json` of each Decision row records which profile was active.
- Personal hard rules stay local and never appear in version control.

**Trade-off:**
- An oracle might consistently recommend instruments the active profile forbids, producing zero proposals for that oracle. This is intentional: the oracle still scores, and the score difference is visible in `committee dissent` even if no trade is proposed.
- Profile selection is manual (`--constraint` flag); there is no automatic account-type detection. The operator decides which profile to apply to which run.
