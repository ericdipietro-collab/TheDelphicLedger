"""Tests for oracle metric computation and scoring.

Uses ~10 synthetic instruments so percentiles are meaningful.
Fixtures avoid any data that would trigger fabricated metrics (Invariant H).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from committee.models import (
    Base,
    Fundamental,
    Holding,
    ImportBatch,
    Instrument,
    MarketObservation,
)


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _instrument(session: Session, ticker: str, itype: str = "stock", sleeve: str = "equity_us") -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=ticker,
        instrument_type=itype,
        sleeve=sleeve,
        is_cash_equivalent=False,
        needs_unwind=False,
        aliases=[],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


def _price_obs(session: Session, inst: Instrument, value: float, obs_date: date | None = None) -> None:
    session.add(MarketObservation(
        source="test",
        series_id=f"price_{inst.ticker}",
        instrument_id=inst.id,
        observed_date=obs_date or date.today(),
        value=Decimal(str(value)),
        unit="USD_adj_close",
        degraded=False,
    ))


def _fundamental(session: Session, inst: Instrument, metric: str, value: float,
                  period_end: date | None = None) -> None:
    session.add(Fundamental(
        instrument_id=inst.id,
        period_end=period_end or date(2024, 12, 31),
        filed_at=date(2025, 1, 15),
        metric=metric,
        value=Decimal(str(value)),
        unit="USD",
    ))


def _holding(session: Session, inst: Instrument, qty: float, market_value: float,
             account_id: str = "acct1") -> None:
    batch = ImportBatch(
        file_hash=str(uuid.uuid4()),
        original_filename="test.csv",
        file_type="positions",
        row_count=1,
    )
    session.add(batch)
    session.flush()
    session.add(Holding(
        instrument_id=inst.id,
        account_id=account_id,
        as_of=date.today(),
        qty=Decimal(str(qty)),
        market_value=Decimal(str(market_value)),
    ))


# ── Scoring unit tests ─────────────────────────────────────────────────────────

def test_percentile_score_higher_is_better():
    from committee.oracles.scoring import percentile_score
    universe = [1.0, 2.0, 3.0, 4.0, 5.0]
    # 5.0 is the highest → best → +1
    s_top = percentile_score(5.0, universe, higher_is_better=True)
    assert s_top >= 0.8
    # 1.0 is the lowest → worst → -1
    s_bot = percentile_score(1.0, universe, higher_is_better=True)
    assert s_bot <= -0.8
    # Median (3.0) → ~0
    s_mid = percentile_score(3.0, universe, higher_is_better=True)
    assert abs(s_mid) < 0.1


def test_percentile_score_lower_is_better():
    from committee.oracles.scoring import percentile_score
    universe = [10.0, 20.0, 30.0, 40.0, 50.0]
    # Lowest P/E (10) is best → +1
    s_cheap = percentile_score(10.0, universe, higher_is_better=False)
    assert s_cheap >= 0.8
    # Highest P/E (50) is worst → -1
    s_expen = percentile_score(50.0, universe, higher_is_better=False)
    assert s_expen <= -0.8


def test_na_for_fund_equity_fundamental():
    from committee.oracles.scoring import score_holding
    metric_weights = {"pe_ratio": 1.0}
    raw_metrics: dict[str, float | None] = {"pe_ratio": 15.0}
    universe_raw = {"pe_ratio": [10.0, 15.0, 20.0]}
    score, metric_scores, _ = score_holding(
        instrument_id=1,
        instrument_type="etf",   # fund → equity fundamentals are n/a
        raw_metrics=raw_metrics,
        universe_raw=universe_raw,
        metric_weights=metric_weights,
        abstain_floor=0.5,
    )
    assert score is None  # abstains because no applicable metrics
    assert metric_scores["pe_ratio"] == "n/a"


def test_na_for_stock_fund_specific():
    from committee.oracles.scoring import score_holding
    metric_weights = {"expense_ratio": 1.0}
    raw_metrics: dict[str, float | None] = {"expense_ratio": 0.003}
    universe_raw = {"expense_ratio": [0.001, 0.003, 0.010]}
    score, metric_scores, _ = score_holding(
        instrument_id=2,
        instrument_type="stock",  # fund-specific metric → n/a
        raw_metrics=raw_metrics,
        universe_raw=universe_raw,
        metric_weights=metric_weights,
        abstain_floor=0.5,
    )
    assert score is None
    assert metric_scores["expense_ratio"] == "n/a"


def test_abstain_below_floor():
    from committee.oracles.scoring import score_holding
    # Two metrics, only one has data (50% of weight)
    metric_weights = {"pe_ratio": 0.60, "pb_ratio": 0.40}
    raw_metrics = {"pe_ratio": 15.0, "pb_ratio": None}
    universe_raw = {"pe_ratio": [10.0, 15.0, 20.0]}
    score, _, _ = score_holding(
        instrument_id=1,
        instrument_type="stock",
        raw_metrics=raw_metrics,
        universe_raw=universe_raw,
        metric_weights=metric_weights,
        abstain_floor=0.9,  # need 90% applicable weight → pe_ratio is only 60% → abstain
    )
    assert score is None


def test_score_with_sufficient_weight():
    from committee.oracles.scoring import score_holding
    metric_weights = {"pe_ratio": 0.60, "pb_ratio": 0.40}
    raw_metrics = {"pe_ratio": 10.0, "pb_ratio": None}
    universe_raw = {"pe_ratio": [10.0, 20.0, 30.0]}
    score, _, reasons = score_holding(
        instrument_id=1,
        instrument_type="stock",
        raw_metrics=raw_metrics,
        universe_raw=universe_raw,
        metric_weights=metric_weights,
        abstain_floor=0.5,  # 60% >= 50% → does not abstain
    )
    assert score is not None
    assert score > 0  # lowest P/E in universe → positive score
    assert any("pe_ratio" in r for r in reasons)


# ── Metric computation unit tests ─────────────────────────────────────────────

def test_pe_ratio_computed(db_session):
    from committee.oracles.metrics import compute_universe_metrics
    inst = _instrument(db_session, "AAPL")
    _price_obs(db_session, inst, 180.0)
    _fundamental(db_session, inst, "eps_diluted", 6.0)
    db_session.flush()

    metrics = compute_universe_metrics(db_session)
    m = metrics.get(inst.id, {})
    assert m.get("pe_ratio") is not None
    assert abs(m["pe_ratio"] - 30.0) < 0.01  # 180/6 = 30


def test_pe_ratio_none_for_negative_eps(db_session):
    from committee.oracles.metrics import compute_universe_metrics
    inst = _instrument(db_session, "LOSS")
    _price_obs(db_session, inst, 10.0)
    _fundamental(db_session, inst, "eps_diluted", -1.0)  # negative EPS
    db_session.flush()

    metrics = compute_universe_metrics(db_session)
    assert metrics[inst.id]["pe_ratio"] is None  # n/a for negative EPS


def test_debt_equity_computed(db_session):
    from committee.oracles.metrics import compute_universe_metrics
    inst = _instrument(db_session, "HIGH_LEV")
    _fundamental(db_session, inst, "total_debt", 200.0)
    _fundamental(db_session, inst, "equity", 100.0)
    db_session.flush()

    metrics = compute_universe_metrics(db_session)
    m = metrics.get(inst.id, {})
    assert m.get("debt_equity") is not None
    assert abs(m["debt_equity"] - 2.0) < 0.01


def test_revenue_yoy_computed(db_session):
    from committee.oracles.metrics import compute_universe_metrics
    inst = _instrument(db_session, "GRWR")
    _fundamental(db_session, inst, "revenue", 120.0, date(2024, 12, 31))
    _fundamental(db_session, inst, "revenue", 100.0, date(2023, 12, 31))
    db_session.flush()

    metrics = compute_universe_metrics(db_session)
    m = metrics.get(inst.id, {})
    assert m.get("revenue_yoy") is not None
    assert abs(m["revenue_yoy"] - 0.20) < 0.01  # 20% growth


def test_dividend_yield_computed(db_session):
    from committee.oracles.metrics import compute_universe_metrics
    inst = _instrument(db_session, "DIVX")
    _price_obs(db_session, inst, 100.0)
    today = date.today()
    for months_ago in [2, 5, 8, 11]:
        session_date = today - timedelta(days=months_ago * 30)
        db_session.add(MarketObservation(
            source="test",
            series_id=f"div_{inst.ticker}",
            instrument_id=inst.id,
            observed_date=session_date,
            value=Decimal("0.50"),
            unit="USD_dividend",
            degraded=False,
        ))
    db_session.flush()

    metrics = compute_universe_metrics(db_session)
    m = metrics.get(inst.id, {})
    assert m.get("dividend_yield") is not None
    assert abs(m["dividend_yield"] - 0.02) < 0.005  # $2 annual / $100 price = 2%


def test_expense_ratio_computed(db_session):
    from committee.oracles.metrics import compute_universe_metrics
    inst = _instrument(db_session, "VOO", itype="etf", sleeve="equity_us")
    db_session.add(MarketObservation(
        source="test",
        series_id="expense_ratio_VOO",
        instrument_id=inst.id,
        observed_date=date.today(),
        value=Decimal("0.0003"),
        unit="distribution_yield",  # note: expense_ratio stored as fundamental
        degraded=False,
    ))
    _fundamental(db_session, inst, "expense_ratio", 0.0003)
    db_session.flush()

    metrics = compute_universe_metrics(db_session)
    m = metrics.get(inst.id, {})
    assert m.get("expense_ratio") is not None
    assert abs(m["expense_ratio"] - 0.0003) < 1e-6


# ── Oracle runner integration tests ───────────────────────────────────────────

def _setup_portfolio(session: Session) -> list[Instrument]:
    """10 synthetic instruments with P/E, D/E, and price data."""
    insts = []
    pe_values = [8, 12, 15, 18, 22, 28, 35, 42, 55, 80]
    de_values = [0.1, 0.3, 0.5, 0.7, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    prices = [50, 80, 100, 120, 150, 180, 200, 250, 300, 400]

    for i, (pe, de, price) in enumerate(zip(pe_values, de_values, prices, strict=True)):
        inst = _instrument(session, f"SYN{i:02d}")
        _price_obs(session, inst, float(price))
        eps = price / pe
        _fundamental(session, inst, "eps_diluted", eps)
        equity = 100.0
        _fundamental(session, inst, "total_debt", de * equity)
        _fundamental(session, inst, "equity", equity)
        _holding(session, inst, qty=10.0, market_value=price * 10)
        insts.append(inst)

    session.flush()
    return insts


def test_value_purist_scores_stocks(db_session):
    from committee.oracles.base import load_oracle_config
    from committee.oracles.runner import run_oracle

    _setup_portfolio(db_session)
    config = load_oracle_config("value_purist")
    output = run_oracle(config, db_session)

    assert output.oracle_id == "value_purist"
    assert not output.abstained
    assert len(output.per_holding_scores) > 0

    # Lowest P/E + D/E instruments should score higher
    scores = {iid: hs.score for iid, hs in output.per_holding_scores.items()
              if hs.score is not None}
    # Some instruments scored positive and negative (spread across universe)
    assert any(s > 0 for s in scores.values())
    assert any(s < 0 for s in scores.values())


def test_passive_pragmatist_scores_expense_ratio(db_session):
    from committee.oracles.base import load_oracle_config
    from committee.oracles.runner import run_oracle

    # Add ETF instruments with different expense ratios
    for ticker, er, price in [("VTSAX", 0.0003, 100), ("FXAIX", 0.0015, 150), ("SPYX", 0.005, 200)]:
        inst = _instrument(db_session, ticker, itype="etf")
        _price_obs(db_session, inst, float(price))
        _fundamental(db_session, inst, "expense_ratio", er)
        _holding(db_session, inst, qty=5.0, market_value=price * 5)

    db_session.flush()
    config = load_oracle_config("passive_pragmatist")
    output = run_oracle(config, db_session)

    assert not output.abstained


def test_oracle_config_loaded():
    from committee.oracles.base import load_all_oracle_configs
    configs = load_all_oracle_configs()
    assert len(configs) == 6
    ids = {c.id for c in configs}
    assert "value_purist" in ids
    assert "macro_tactician" in ids
    assert "passive_pragmatist" in ids


def test_macro_tactician_returns_sleeve_targets(db_session):
    from committee.oracles.base import load_oracle_config
    from committee.oracles.runner import run_oracle

    config = load_oracle_config("macro_tactician")
    output = run_oracle(config, db_session)

    assert output.oracle_id == "macro_tactician"
    assert output.per_holding_scores == {}  # no per-holding scores
    assert "equity_us" in output.sleeve_targets
    total = sum(output.sleeve_targets.values())
    assert abs(float(total) - 1.0) < 0.01


def test_oracle_does_not_import_rebalancer():
    """Invariant A: oracle source must not contain forbidden import statements."""
    import ast
    from pathlib import Path

    oracles_dir = Path(__file__).parent.parent / "src" / "committee" / "oracles"
    for py_file in oracles_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "committee.rebalancer" not in alias.name, (
                        f"{py_file.name} imports committee.rebalancer"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "committee.rebalancer" not in module, (
                    f"{py_file.name} imports from committee.rebalancer"
                )
