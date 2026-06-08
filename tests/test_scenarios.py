"""Tests for the M5 scenario pack system.

Key invariant: identical inputs must produce byte-identical Decision content
(inputs_json, outputs_json, proposals_json). The test verifies this by running
the scenario engine twice and comparing field content *without sort_keys=True* —
sorting in the comparison would mask the non-determinism we are guarding against.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from committee.core.types import ScenarioContext
from committee.models import (
    Account,
    Base,
    Holding,
    ImportBatch,
    Instrument,
    MarketObservation,
    RegimeState,
)
from committee.oracles.runner import run_all_oracles
from committee.rebalancer.engine import RebalanceParams, propose
from committee.rebalancer.profiles import ConstraintProfile
from committee.scenarios.loader import list_packs, load_pack

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/scenario_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _acct(session: Session, key: str, tax_type: str = "taxable") -> Account:
    a = Account(account_key=key, tax_type=tax_type)
    session.add(a)
    session.flush()
    return a


def _inst(
    session: Session,
    ticker: str,
    itype: str = "etf",
    sleeve: str = "equity_us",
) -> Instrument:
    i = Instrument(
        ticker=ticker,
        instrument_type=itype,
        sleeve=sleeve,
        is_cash_equivalent=False,
        needs_unwind=False,
        aliases=[],
        bundle_tags=[],
    )
    session.add(i)
    session.flush()
    return i


def _holding(
    session: Session,
    inst: Instrument,
    qty: float,
    mv: float,
    acct: Account,
) -> None:
    batch = ImportBatch(
        file_hash=str(uuid.uuid4()),
        original_filename="test.csv",
        file_type="positions",
        row_count=1,
    )
    session.add(batch)
    session.flush()
    session.add(
        Holding(
            instrument_id=inst.id,
            account_id=acct.account_key,
            as_of=date.today(),
            qty=Decimal(str(qty)),
            market_value=Decimal(str(mv)),
        )
    )


def _price_obs(session: Session, inst: Instrument, price: float) -> None:
    session.add(
        MarketObservation(
            instrument_id=inst.id,
            series_id=f"price_{inst.ticker}",
            observed_date=date.today(),
            value=Decimal(str(price)),
            unit="USD_adj_close",
            degraded=False,
            source="test",
        )
    )


def _macro_obs(session: Session, series_id: str, value: float) -> None:
    session.add(
        MarketObservation(
            instrument_id=None,
            series_id=series_id,
            observed_date=date.today(),
            value=Decimal(str(value)),
            unit="FRED",
            degraded=False,
            source="FRED",
        )
    )


def _seed_regime(session: Session) -> None:
    session.add(RegimeState(id=1, tilt="neutral", pending_tilt=None, confirmation_count=0))
    session.flush()


def _build_scenario_db(session: Session) -> ScenarioContext:
    """Seed a minimal multi-sleeve portfolio and return a GFC-like scenario context."""
    acct = _acct(session, "main", "taxable")

    eq_us = _inst(session, "VTI", sleeve="equity_us")
    eq_intl = _inst(session, "VXUS", sleeve="equity_intl")
    bonds = _inst(session, "BND", sleeve="fixed_income")

    _holding(session, eq_us, qty=100, mv=10000, acct=acct)
    _holding(session, eq_intl, qty=50, mv=5000, acct=acct)
    _holding(session, bonds, qty=80, mv=8000, acct=acct)

    _price_obs(session, eq_us, 100.0)
    _price_obs(session, eq_intl, 100.0)
    _price_obs(session, bonds, 100.0)

    # Minimal macro series so Macro Tactician doesn't abstain on DB path
    _macro_obs(session, "T10Y3M", 0.5)
    _macro_obs(session, "BAMLH0A0HYM2", 3.5)
    _macro_obs(session, "VIXCLS", 18.0)
    _macro_obs(session, "UNRATE", 4.0)

    _seed_regime(session)
    session.flush()

    return ScenarioContext(
        pack_id="test-gfc",
        sleeve_shocks={
            "equity_us": Decimal("-0.57"),
            "equity_intl": Decimal("-0.57"),
            "fixed_income": Decimal("-0.04"),
        },
        indicator_overrides={
            "T10Y3M": -0.32,
            "BAMLH0A0HYM2": 18.5,
            "VIXCLS": 80.86,
            "UNRATE": 10.0,
        },
    )


# ---------------------------------------------------------------------------
# Loader tests
# ---------------------------------------------------------------------------


def test_list_packs_finds_all_six(tmp_path):
    """Six shipped packs are present in the scenarios/ directory."""
    scenarios_dir = Path(__file__).parent.parent / "scenarios"
    packs = list_packs(scenarios_dir)
    expected = {
        "black-monday-1987",
        "dot-com-2000",
        "gfc-2008",
        "covid-2020",
        "ai-bubble",
        "rate-shock-300bps",
    }
    assert expected.issubset(set(packs)), f"Missing packs: {expected - set(packs)}"


def test_load_gfc_pack():
    scenarios_dir = Path(__file__).parent.parent / "scenarios"
    pack = load_pack("gfc-2008", scenarios_dir)
    assert pack.pack_id == "gfc-2008"
    assert pack.pack_type == "historical"
    assert pack.honesty_note  # non-empty
    assert len(pack.sources) >= 4
    assert "equity_us" in pack.context.sleeve_shocks
    assert pack.context.sleeve_shocks["equity_us"] < 0
    assert "VIXCLS" in pack.context.indicator_overrides


def test_load_hypothetical_packs():
    scenarios_dir = Path(__file__).parent.parent / "scenarios"
    for pack_id in ("ai-bubble", "rate-shock-300bps"):
        pack = load_pack(pack_id, scenarios_dir)
        assert pack.pack_type == "hypothetical", f"{pack_id} should be hypothetical"


def test_all_packs_have_sleeve_shocks_for_canonical_sleeves():
    """Every pack only references valid sleeve names."""
    canonical = {"equity_us", "equity_intl", "fixed_income", "alternatives", "cash"}
    scenarios_dir = Path(__file__).parent.parent / "scenarios"
    for pack_id in list_packs(scenarios_dir):
        pack = load_pack(pack_id, scenarios_dir)
        for sleeve in pack.context.sleeve_shocks:
            assert sleeve in canonical, (
                f"Pack {pack_id} has unknown sleeve '{sleeve}'"
            )


def test_load_pack_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_pack("does-not-exist", Path("scenarios"))


# ---------------------------------------------------------------------------
# Arithmetic shock tests
# ---------------------------------------------------------------------------


def test_sleeve_shock_changes_portfolio_mv(db_session):
    """After a −57% equity_us shock, shocked MV < real MV."""
    scenario = _build_scenario_db(db_session)

    from committee.oracles.metrics import compute_universe_metrics

    real_metrics = compute_universe_metrics(db_session)
    shocked_metrics = compute_universe_metrics(db_session, scenario=scenario)

    # VTI (equity_us) P/E uses shocked price; BND (fixed_income) less shocked
    # We can't easily check P/E without EPS data, but we can verify
    # concentration_hhi changed (weights shifted due to shocked MVs)
    vti_iid = db_session.query(Instrument).filter_by(ticker="VTI").one().id

    real_vti_weight = real_metrics[vti_iid]["concentration_hhi"]
    shocked_vti_weight = shocked_metrics[vti_iid]["concentration_hhi"]

    # equity_us shocked −57%, bonds −4%, so equity_us weight drops relative to bonds
    assert shocked_vti_weight < real_vti_weight, (
        "VTI (equity_us) weight^2 should drop under GFC shock"
    )


def test_shock_does_not_mutate_db(db_session):
    """Running a scenario does not change any holding market values in the DB."""
    from committee.models import Holding

    scenario = _build_scenario_db(db_session)
    before = {h.id: h.market_value for h in db_session.query(Holding).all()}

    run_all_oracles(db_session, scenario=scenario)

    after = {h.id: h.market_value for h in db_session.query(Holding).all()}
    assert before == after, "Scenario run must not mutate holding market values"


def test_regime_state_not_persisted_in_scenario(db_session):
    """Scenario runs must not update the regime_state row (no side effects)."""
    scenario = _build_scenario_db(db_session)

    before = db_session.get(RegimeState, 1)
    before_tilt = before.tilt if before else "neutral"
    before_count = before.confirmation_count if before else 0

    # GFC scenario has extreme VIXCLS=80.86 and HY OAS=18.5 — should trigger defensive,
    # but persist_regime=False means it MUST NOT save
    run_all_oracles(db_session, scenario=scenario)
    db_session.flush()

    after = db_session.get(RegimeState, 1)
    assert (after.tilt if after else "neutral") == before_tilt
    assert (after.confirmation_count if after else 0) == before_count


# ---------------------------------------------------------------------------
# Determinism test — the load-bearing invariant
# ---------------------------------------------------------------------------


def _run_scenario_and_serialize(db_session: Session, scenario: ScenarioContext) -> dict:
    """Run all oracles + rebalancer under a scenario; serialize content fields.

    Intentionally does NOT use json.dumps(sort_keys=True) — that would mask
    non-determinism in dict construction order.
    """
    profile = ConstraintProfile(
        id="unconstrained", display_name="Unconstrained", description="test", allowed_buy_types=[]
    )
    oracle_outputs = run_all_oracles(db_session, scenario=scenario)

    result = {}
    for output in oracle_outputs:
        proposals = propose(
            output, db_session,
            constraint_profile=profile,
            params=RebalanceParams(),
            scenario=scenario,
        )
        inputs = {
            "sleeve_shocks": {k: str(v) for k, v in sorted(scenario.sleeve_shocks.items())},
            "indicator_overrides": {k: v for k, v in sorted(scenario.indicator_overrides.items())},
        }
        outputs = {
            "scores": {
                str(iid): {"score": hs.score, "reasons": hs.reasons}
                for iid, hs in sorted(output.per_holding_scores.items())
            },
            "sleeve_targets": {k: str(v) for k, v in sorted(output.sleeve_targets.items())},
            "abstained": output.abstained,
        }
        proposals_data = [
            {
                "instrument_id": p.instrument_id,
                "account_id": p.account_id,
                "direction": p.direction,
                "qty": str(p.qty),
                "estimated_value": str(p.estimated_value),
                "tags": p.rationale_tags,
                "oracle_score": p.oracle_score,
                "tax_note": p.tax_note,
            }
            for p in proposals
        ]
        result[output.oracle_id] = {
            "inputs": inputs,
            "outputs": outputs,
            "proposals": proposals_data,
        }
    return result


def test_scenario_determinism(db_session):
    """Identical inputs must produce byte-identical serialized Decision content.

    This test intentionally does NOT use sort_keys=True on the outer comparison —
    if dict ordering is non-deterministic, json.dumps without sort_keys will reveal it.
    """
    scenario = _build_scenario_db(db_session)

    run1 = _run_scenario_and_serialize(db_session, scenario)
    run2 = _run_scenario_and_serialize(db_session, scenario)

    for oracle_id in run1:
        for field in ("inputs", "outputs", "proposals"):
            s1 = json.dumps(run1[oracle_id][field])
            s2 = json.dumps(run2[oracle_id][field])
            assert s1 == s2, (
                f"Non-determinism detected in oracle={oracle_id} field={field}.\n"
                f"Run 1: {s1[:200]}\nRun 2: {s2[:200]}"
            )


# ---------------------------------------------------------------------------
# Scenario context immutability
# ---------------------------------------------------------------------------


def test_scenario_context_is_frozen():
    ctx = ScenarioContext(
        pack_id="test",
        sleeve_shocks={"equity_us": Decimal("-0.30")},
        indicator_overrides={"VIXCLS": 45.0},
    )
    with pytest.raises((AttributeError, TypeError)):
        ctx.pack_id = "mutated"  # type: ignore[misc]
