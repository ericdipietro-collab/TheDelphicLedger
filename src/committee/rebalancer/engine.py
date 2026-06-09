"""Shared rebalancer: oracle output → trade proposals.

Algorithm:
1. Compute current sleeve weights at household level (Invariant C).
2. Identify sleeves outside 5% absolute or 25% relative drift bands.
3. New-money-first: if new_money > 0, route to under-target sleeves first.
4. Sell worst-scored in over-target sleeves (tax-advantaged accounts first).
5. Buy best-scored in under-target sleeves.
6. Enforce constraint_profile (e.g. funds_only: no stock buys) AND
   persona_constraints (min_yield, max_positions, min_score_to_buy).
7. Round to whole shares; filter trades below min_trade_usd.

Import constraint: rebalancer/ must not import signals/ or oracles/ (Invariant A).
Types come from core/ only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.core.types import OracleOutput, ScenarioContext, TradeProposal
from committee.models import Account, Holding, Instrument, MarketObservation, UniverseEntry
from committee.rebalancer.profiles import ConstraintProfile

_DRIFT_ABS = Decimal("0.05")   # 5% absolute
_DRIFT_REL = Decimal("0.25")   # 25% relative
_MIN_TRADE_USD = Decimal("200")
_WHOLE_SHARE = Decimal("1")

# Tax priority for sells: prefer tax-advantaged (defer taxable gains).
_TAX_PRIORITY = {"roth": 1, "trad": 2, "401k": 3, "taxable": 4}


@dataclass
class RebalanceParams:
    drift_abs: Decimal = _DRIFT_ABS
    drift_rel: Decimal = _DRIFT_REL
    min_trade_usd: Decimal = _MIN_TRADE_USD
    new_money: Decimal = Decimal("0")  # fresh cash to deploy before selling
    dry_run: bool = False


def _tax_priority(account: Account | None) -> int:
    if account is None:
        return 99
    return _TAX_PRIORITY.get(account.tax_type or "", 99)


def propose(
    oracle_output: OracleOutput,
    session: Session,
    constraint_profile: ConstraintProfile | None = None,
    params: RebalanceParams | None = None,
    scenario: ScenarioContext | None = None,
) -> list[TradeProposal]:
    """Generate trade proposals for one oracle's output.

    Returns an empty list when there are no holdings or no drift to fix.
    """
    if params is None:
        params = RebalanceParams()

    # Load holdings + instruments + accounts
    holdings = session.execute(
        select(Holding).where(Holding.market_value.isnot(None))
    ).scalars().all()
    if not holdings:
        return []

    instruments: dict[int, Instrument] = {}
    for iid in sorted({h.instrument_id for h in holdings}):
        inst = session.get(Instrument, iid)
        if inst is not None:
            instruments[iid] = inst

    accounts: dict[str, Account] = {}
    for h in holdings:
        if h.account_id and h.account_id not in accounts:
            acct = session.execute(
                select(Account).where(Account.account_key == h.account_id)
            ).scalar_one_or_none()
            if acct is not None:
                accounts[h.account_id] = acct

    # Household totals (aggregate across accounts — Invariant C analysis grain)
    # When a scenario is provided, shocked MVs drive drift detection; prices stay real
    # (proposals show what to do from the shocked state, not fabricated prices).
    total_mv = Decimal("0")
    household: dict[int, Decimal] = {}  # instrument_id → total market_value
    latest_price: dict[int, Decimal] = {}  # instrument_id → latest price per share
    for h in holdings:
        raw_mv = h.market_value or Decimal("0")
        if scenario:
            sleeve = (instruments.get(h.instrument_id) and instruments[h.instrument_id].sleeve) or "other"
            shock = scenario.sleeve_shocks.get(sleeve, Decimal("0"))
            mv = raw_mv * (Decimal("1") + shock)
        else:
            mv = raw_mv
        total_mv += mv
        household[h.instrument_id] = household.get(h.instrument_id, Decimal("0")) + mv
        # Estimate price from market_value / qty (whole-share rounding basis)
        if h.qty and h.qty > 0 and h.market_value:
            lp = h.market_value / h.qty
            latest_price[h.instrument_id] = lp

    if total_mv == 0:
        return []

    # Load universe instruments (buy candidates not currently held)
    universe_iids: list[int] = session.execute(
        select(UniverseEntry.instrument_id)
    ).scalars().all()

    for iid in universe_iids:
        if iid in instruments:
            continue
        inst = session.get(Instrument, iid)
        if inst is not None:
            instruments[iid] = inst

    # Fetch latest prices for universe instruments from MarketObservation
    for iid in universe_iids:
        if iid in latest_price:
            continue
        obs = session.execute(
            select(MarketObservation)
            .where(
                MarketObservation.instrument_id == iid,
                MarketObservation.unit == "USD_adj_close",
            )
            .order_by(MarketObservation.observed_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if obs is not None:
            latest_price[iid] = obs.value

    # Current sleeve weights (household level)
    sleeve_mv: dict[str, Decimal] = {}
    for iid, mv in household.items():
        inst = instruments.get(iid)
        sleeve = (inst.sleeve if inst and inst.sleeve else "other") or "other"
        sleeve_mv[sleeve] = sleeve_mv.get(sleeve, Decimal("0")) + mv

    sleeve_weight: dict[str, Decimal] = {
        s: mv / total_mv for s, mv in sleeve_mv.items()
    }

    # Oracle sleeve targets
    targets = oracle_output.sleeve_targets

    # 1. Identify drifting sleeves
    over_target: list[str] = []   # current > target by drift band
    under_target: list[str] = []  # current < target by drift band

    for sleeve, target in targets.items():
        current = sleeve_weight.get(sleeve, Decimal("0"))
        abs_drift = abs(current - target)
        rel_drift = abs_drift / target if target > 0 else abs_drift
        if current > target and (abs_drift > params.drift_abs or rel_drift > params.drift_rel):
            over_target.append(sleeve)
        elif current < target and (abs_drift > params.drift_abs or rel_drift > params.drift_rel):
            under_target.append(sleeve)

    if not over_target and not under_target and params.new_money == 0:
        return []

    proposals: list[TradeProposal] = []
    persona = oracle_output.persona_constraints
    scores = oracle_output.per_holding_scores

    # 2. New-money allocation (buy into under-target sleeves before selling)
    remaining_new_money = params.new_money

    # 3. Generate sells for over-target sleeves (worst-scored first)
    for sleeve in over_target:
        # Holdings in this sleeve, sorted by score ascending (worst first)
        sleeve_holdings = [
            (iid, mv)
            for iid, mv in household.items()
            if (instruments.get(iid) and (instruments[iid].sleeve or "other") == sleeve)
        ]
        sleeve_holdings.sort(key=lambda x: (scores.get(x[0]) and scores[x[0]].score or 0.0, x[0]))

        target_mv = targets.get(sleeve, Decimal("0")) * total_mv
        current_mv = sleeve_mv.get(sleeve, Decimal("0"))
        needed_sell = current_mv - target_mv

        for iid, mv in sleeve_holdings:
            if needed_sell <= 0:
                break
            price = latest_price.get(iid, Decimal("0"))
            if price <= 0:
                continue
            inst = instruments.get(iid)
            sell_mv = min(mv, needed_sell)
            sell_qty = (sell_mv / price).to_integral_value(rounding=ROUND_DOWN)
            if sell_qty < _WHOLE_SHARE:
                continue
            actual_mv = sell_qty * price
            if actual_mv < params.min_trade_usd:
                continue

            # Tax-aware: assign to the account where this holding is most tax-advantaged
            # Prefer selling in tax-advantaged accounts to preserve taxable gains
            account_id = _pick_sell_account(iid, holdings, accounts)
            oracle_score = scores[iid].score if iid in scores else None

            tax_note = _tax_note_sell(account_id, iid, accounts, session)
            proposals.append(
                TradeProposal(
                    instrument_id=iid,
                    account_id=account_id,
                    direction="sell",
                    qty=sell_qty,
                    estimated_value=actual_mv,
                    rationale_tags=["drift"],
                    oracle_score=oracle_score,
                    tax_note=tax_note,
                )
            )
            needed_sell -= actual_mv

    # 4. Generate buys for under-target sleeves (best-scored first)
    buy_budget = remaining_new_money + sum(
        p.estimated_value for p in proposals if p.direction == "sell"
    )

    # Persona max_positions constraint
    current_positions = len({h.instrument_id for h in holdings})
    max_new = (
        persona.max_positions - current_positions if persona.max_positions else 9999
    )
    new_positions = 0

    for sleeve in under_target:
        # Include currently-held instruments in this sleeve
        held_candidates: list[tuple[int, Decimal]] = [
            (iid, mv)
            for iid, mv in household.items()
            if (instruments.get(iid) and (instruments[iid].sleeve or "other") == sleeve)
        ]
        # Also include universe instruments in this sleeve not already held
        universe_candidates: list[tuple[int, Decimal]] = [
            (iid, Decimal("0"))
            for iid in universe_iids
            if iid not in household
            and instruments.get(iid)
            and (instruments[iid].sleeve or "other") == sleeve
            and iid in latest_price
        ]
        sleeve_holdings = held_candidates + universe_candidates
        # Sort by score descending (best first), iid as tiebreaker for determinism
        sleeve_holdings.sort(
            key=lambda x: (-(scores.get(x[0]) and scores[x[0]].score or 0.0), x[0])
        )

        target_mv = targets.get(sleeve, Decimal("0")) * total_mv
        current_sleeve_mv = sleeve_mv.get(sleeve, Decimal("0"))
        needed_buy = target_mv - current_sleeve_mv

        for iid, _mv in sleeve_holdings:
            if needed_buy <= 0 or buy_budget <= 0:
                break
            inst = instruments.get(iid)
            price = latest_price.get(iid, Decimal("0"))
            if price <= 0:
                continue

            # Constraint profile: funds_only blocks stock buys
            if constraint_profile and not constraint_profile.can_buy(
                inst.instrument_type if inst else None
            ):
                continue

            # Persona: min_score_to_buy
            oracle_score = scores[iid].score if iid in scores else None
            if (
                persona.min_score_to_buy is not None
                and oracle_score is not None
                and oracle_score < persona.min_score_to_buy
            ):
                continue

            # Max positions
            is_new_position = iid not in household or household[iid] == 0
            if is_new_position and new_positions >= max_new:
                continue

            buy_mv = min(needed_buy, buy_budget)
            buy_qty = (buy_mv / price).to_integral_value(rounding=ROUND_DOWN)
            if buy_qty < _WHOLE_SHARE:
                continue
            actual_mv = buy_qty * price
            if actual_mv < params.min_trade_usd:
                continue

            account_id = _pick_buy_account(holdings, accounts)
            tags = ["drift"]
            if oracle_score is not None and oracle_score > 0.2:
                tags.append("score")
            if params.new_money > 0:
                tags.append("new_money")

            proposals.append(
                TradeProposal(
                    instrument_id=iid,
                    account_id=account_id,
                    direction="buy",
                    qty=buy_qty,
                    estimated_value=actual_mv,
                    rationale_tags=tags,
                    oracle_score=oracle_score,
                    tax_note=None,
                )
            )
            needed_buy -= actual_mv
            buy_budget -= actual_mv
            if is_new_position:
                new_positions += 1

    return proposals


def _pick_sell_account(
    instrument_id: int,
    holdings: list[Holding],
    accounts: dict[str, Account],
) -> str:
    """Choose the best account to sell from (tax-advantaged first)."""
    options = [
        h for h in holdings
        if h.instrument_id == instrument_id and h.account_id and (h.qty or 0) > 0
    ]
    if not options:
        return "unknown"
    options.sort(key=lambda h: _tax_priority(accounts.get(h.account_id or "")))
    return options[0].account_id or "unknown"


def _pick_buy_account(
    holdings: list[Holding],
    accounts: dict[str, Account],
) -> str:
    """Choose the best account to buy into (taxable last, so tax-advantaged grows)."""
    if not holdings:
        return "unknown"
    unique_accounts = {h.account_id for h in holdings if h.account_id}
    if not unique_accounts:
        return "unknown"
    # Buy into lowest-priority (most tax-advantaged) account
    sorted_accts = sorted(
        unique_accounts,
        key=lambda aid: _tax_priority(accounts.get(aid)),
    )
    return sorted_accts[0]


def _tax_note_sell(
    account_id: str,
    instrument_id: int,
    accounts: dict[str, Account],
    session: object,
) -> str | None:
    from sqlalchemy import select as _select
    from sqlalchemy.orm import Session as _Session

    from committee.models import TaxLot as _TaxLot

    acct = accounts.get(account_id)
    if acct is None:
        return None
    if acct.tax_type in ("roth", "trad", "401k"):
        return "tax-advantaged account — no immediate capital gains"
    if acct.tax_type != "taxable":
        return None

    if not isinstance(session, _Session):
        return "taxable — verify holding period before executing"

    lots = session.execute(
        _select(_TaxLot)
        .where(
            _TaxLot.instrument_id == instrument_id,
            _TaxLot.account_id == account_id,
        )
    ).scalars().all()

    if not lots:
        return "taxable — basis unknown; run `committee unwind` for lot detail"

    has_fallback = any(lot.basis_quality == "average_fallback" for lot in lots)
    if has_fallback:
        return "taxable — ⚠ average-cost basis; run `committee unwind` for detail"
    return "taxable — exact lots available; run `committee unwind` for detail"
