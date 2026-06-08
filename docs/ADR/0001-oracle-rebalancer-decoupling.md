# ADR 0001: Oracle-Rebalancer Decoupling

**Status:** Accepted  
**Date:** 2024-01-01

## Context

A portfolio analysis tool needs to score holdings and propose trades. The naive implementation puts both responsibilities in the same function: score each holding, then immediately decide what to trade.

The problem: if scoring and trade-proposal logic live together, adding a new oracle requires touching the rebalancer. Changing trade-sizing logic (e.g. adding tax-lot awareness) requires touching every oracle. The two concerns couple at the worst possible place.

## Decision

Oracles emit exactly three things:

```python
per_holding_scores: dict[int, HoldingScore]
sleeve_targets: dict[str, Decimal]
persona_constraints: PersonaConstraints
```

**Oracles never emit trades.** One shared rebalancer (`rebalancer/`) consumes oracle output and produces all `TradeProposal` objects.

This is enforced by an import-linter contract in CI:

```ini
[[tool.importlinter.contracts]]
name = "oracles must not import rebalancer"
type = "forbidden"
source_modules = ["committee.oracles"]
forbidden_modules = ["committee.rebalancer"]

[[tool.importlinter.contracts]]
name = "rebalancer must not import signals or oracles"
type = "forbidden"
source_modules = ["committee.rebalancer"]
forbidden_modules = ["committee.signals", "committee.oracles"]
```

Shared types live in `core/` or `models/` which neither layer owns.

## Consequences

**Good:**
- Adding a seventh oracle requires zero changes to the rebalancer.
- Tax-lot logic, constraint profiles, and drift bands live in one place.
- Oracle outputs are serializable (stored as JSON in `decisions`). The dashboard can reconstruct them without re-running oracles.
- Unit testing oracles and the rebalancer is fully independent.

**Trade-off:**
- Oracles cannot directly optimize trades (e.g. "buy the cheapest lot first"). That optimization belongs in the rebalancer, informed by oracle scores.
- Shared types in `core/` must be stable; changing `HoldingScore` requires coordinating both sides.
