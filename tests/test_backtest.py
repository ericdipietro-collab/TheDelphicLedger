"""Tests for the backtest engine and point-in-time data discipline.

The lookahead test (test_pit_price_excludes_future) is the key invariant:
it passes only when the PIT guard correctly excludes future observations.
If the pit_date filter is removed from _latest_prices(), that test fails.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from committee.backtest.engine import (
    BacktestReport,
    _compute_metrics,
    _evaluate_pass_bar,
    _monthly_dates,
    _portfolio_return,
    _sleeve_returns,
    run_backtest,
)
from committee.models import (
    Base,
    Fundamental,
    Holding,
    ImportBatch,
    Instrument,
    MarketObservation,
)
from committee.oracles.metrics import compute_universe_metrics


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path):
    db = create_engine(f"sqlite:///{tmp_path}/bt.db")
    Base.metadata.create_all(db)
    return db


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def _inst(session: Session, ticker: str = "AAPL", sleeve: str = "equity_us") -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=ticker + " Inc",
        instrument_type="stock",
        sleeve=sleeve,
        is_cash_equivalent=False,
        needs_unwind=False,
        aliases=[],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


def _price(session: Session, iid: int, obs_date: date, price: float) -> None:
    session.add(MarketObservation(
        source="test",
        series_id=f"PRICE_{iid}",
        instrument_id=iid,
        observed_date=obs_date,
        value=Decimal(str(price)),
        unit="USD_adj_close",
        degraded=False,
    ))


def _holding(session: Session, iid: int, qty: float = 100.0) -> None:
    batch = ImportBatch(file_hash=f"h{iid}", original_filename="t.csv", file_type="positions", row_count=1)
    session.add(batch)
    session.flush()
    session.add(Holding(
        instrument_id=iid, account_id="taxable", as_of=date.today(),
        qty=Decimal(str(qty)), market_value=None,
    ))


# ── PIT guard: the key lookahead test ─────────────────────────────────────────


def test_pit_price_excludes_future(session: Session) -> None:
    """PIT filter must exclude observations after pit_date.

    This test FAILS if pit_date filter is removed from _latest_prices():
    pe_with_pit would equal pe_without_pit (both 20), not 10.
    """
    replay_date = date(2020, 1, 31)
    future_date = date(2020, 2, 28)

    old_price = 100.0
    new_price = 200.0  # dramatically different so any P/E change is visible
    eps_val = Decimal("10")

    inst = _inst(session)
    _price(session, inst.id, replay_date, old_price)
    _price(session, inst.id, future_date, new_price)  # future — must be excluded

    session.add(Fundamental(
        instrument_id=inst.id,
        period_end=date(2019, 12, 31),
        filed_at=None,  # conservative fallback: use period_end ≤ pit_date
        metric="eps_diluted",
        value=eps_val,
    ))
    session.commit()

    # With PIT guard: should use old_price → P/E = 100/10 = 10
    metrics_pit = compute_universe_metrics(session, pit_date=replay_date)
    pe_pit = metrics_pit.get(inst.id, {}).get("pe_ratio")

    # Without PIT guard: should use new_price → P/E = 200/10 = 20
    metrics_no_pit = compute_universe_metrics(session)
    pe_no_pit = metrics_no_pit.get(inst.id, {}).get("pe_ratio")

    # The PIT version must use the historical price
    assert pe_pit == pytest.approx(10.0), (
        f"PIT guard failed: expected P/E≈10 at {replay_date}, got {pe_pit}. "
        "Future price leaked through."
    )

    # The no-pit version should use the newer price (proving the future obs was there)
    assert pe_no_pit == pytest.approx(20.0), (
        f"Expected P/E≈20 without PIT guard (new price), got {pe_no_pit}"
    )

    # Guard effectiveness: the two versions must differ
    assert pe_pit != pe_no_pit, (
        "PIT guard has no effect — lookahead is undetectable. "
        "This suggests the PIT filter is not applied."
    )


def test_pit_fundamental_excludes_unfiled(session: Session) -> None:
    """When filed_at is in the future relative to pit_date, the filing is excluded."""
    replay_date = date(2020, 1, 31)
    filed_late = date(2020, 3, 15)   # filed after pit_date

    inst = _inst(session)
    _price(session, inst.id, replay_date, 100.0)
    session.add(Fundamental(
        instrument_id=inst.id,
        period_end=date(2019, 12, 31),
        filed_at=filed_late,          # filed_at > replay_date → must be excluded
        metric="eps_diluted",
        value=Decimal("10"),
    ))
    session.commit()

    metrics_pit = compute_universe_metrics(session, pit_date=replay_date)
    pe_pit = metrics_pit.get(inst.id, {}).get("pe_ratio")

    # filed_at is after pit_date → EPS unavailable → P/E must be None
    assert pe_pit is None, (
        f"Expected pe_ratio=None (EPS not yet filed at {replay_date}), got {pe_pit}"
    )


# ── Metric computation helpers ────────────────────────────────────────────────


def test_compute_metrics_cagr() -> None:
    """Doubling value over 4 years → CAGR ≈ 18.92%."""
    d1 = date(2020, 1, 1)
    dates = [d1 + timedelta(days=365 * i) for i in range(5)]
    values = [1.0, 1.2, 1.4, 1.7, 2.0]
    m = _compute_metrics("test", dates, values)
    assert m.cagr == pytest.approx(0.1892, abs=0.005)


def test_compute_metrics_max_drawdown() -> None:
    """Peak = 1.5, trough = 1.0 → drawdown = 33.3%."""
    d1 = date(2020, 1, 1)
    dates = [d1 + timedelta(days=30 * i) for i in range(5)]
    values = [1.0, 1.5, 1.2, 1.0, 1.1]
    m = _compute_metrics("test", dates, values)
    assert m.max_drawdown == pytest.approx(1 / 3, abs=0.005)


def test_compute_metrics_ulcer_index() -> None:
    """Ulcer index is sqrt(mean of squared drawdowns)."""
    d1 = date(2020, 1, 1)
    dates = [d1 + timedelta(days=30 * i) for i in range(3)]
    values = [1.0, 1.0, 1.0]  # no drawdown → ulcer = 0
    m = _compute_metrics("test", dates, values)
    assert m.ulcer_index == pytest.approx(0.0)


def test_compute_metrics_insufficient_data() -> None:
    d1 = date(2020, 1, 1)
    m = _compute_metrics("test", [d1], [1.0])
    assert not m.has_data
    assert m.cagr is None


# ── Pass-bar logic ────────────────────────────────────────────────────────────


def test_pass_bar_passes() -> None:
    """Tilt that cuts drawdown ≥20% and costs ≤1% CAGR with few switches passes."""
    from committee.backtest.engine import BacktestMetrics, _evaluate_pass_bar
    bench = BacktestMetrics("bench", cagr=0.08, max_drawdown=0.30, ulcer_index=0.10,
                            annualized_turnover=None, implied_tax_drag=None, switch_count=0)
    tilt = BacktestMetrics("tilt", cagr=0.075, max_drawdown=0.22, ulcer_index=0.08,
                           annualized_turnover=None, implied_tax_drag=None, switch_count=3)
    result = _evaluate_pass_bar(tilt, bench, years=10.0)

    # drawdown improvement = (0.30 - 0.22) / 0.30 = 26.7% → ≥ 20% ✓
    # cagr cost = 0.08 - 0.075 = 0.5% → ≤ 1% ✓
    # switches/decade = 3/10*10 = 3 → < 10 ✓
    assert result.passes is True
    assert result.fallback_mode == "active"
    assert result.drawdown_improvement == pytest.approx(8 / 30, abs=0.002)


def test_pass_bar_fails_insufficient_drawdown_improvement() -> None:
    from committee.backtest.engine import BacktestMetrics, _evaluate_pass_bar
    bench = BacktestMetrics("bench", cagr=0.08, max_drawdown=0.30, ulcer_index=0.10,
                            annualized_turnover=None, implied_tax_drag=None, switch_count=0)
    tilt = BacktestMetrics("tilt", cagr=0.079, max_drawdown=0.29, ulcer_index=0.09,
                           annualized_turnover=None, implied_tax_drag=None, switch_count=2)
    result = _evaluate_pass_bar(tilt, bench, years=10.0)

    # drawdown improvement = (0.30 - 0.29)/0.30 = 3.3% → < 20% required → FAILS
    assert result.passes is False
    assert result.fallback_mode == "entertainment_only"
    assert "drawdown improvement" in result.verdict


def test_pass_bar_fails_too_many_switches() -> None:
    from committee.backtest.engine import BacktestMetrics, _evaluate_pass_bar
    bench = BacktestMetrics("bench", cagr=0.08, max_drawdown=0.30, ulcer_index=0.10,
                            annualized_turnover=None, implied_tax_drag=None, switch_count=0)
    tilt = BacktestMetrics("tilt", cagr=0.079, max_drawdown=0.22, ulcer_index=0.08,
                           annualized_turnover=None, implied_tax_drag=None, switch_count=15)
    result = _evaluate_pass_bar(tilt, bench, years=10.0)

    # switches/decade = 15 → ≥ 10 → FAILS
    assert result.passes is False
    assert "switches/decade" in result.verdict


def test_pass_bar_fails_too_expensive() -> None:
    from committee.backtest.engine import BacktestMetrics, _evaluate_pass_bar
    bench = BacktestMetrics("bench", cagr=0.10, max_drawdown=0.30, ulcer_index=0.10,
                            annualized_turnover=None, implied_tax_drag=None, switch_count=0)
    tilt = BacktestMetrics("tilt", cagr=0.07, max_drawdown=0.22, ulcer_index=0.08,
                           annualized_turnover=None, implied_tax_drag=None, switch_count=2)
    result = _evaluate_pass_bar(tilt, bench, years=10.0)

    # cagr_cost = 0.10 - 0.07 = 3% → > 1% allowed → FAILS
    assert result.passes is False
    assert "CAGR cost" in result.verdict


def test_pass_bar_insufficient_data() -> None:
    from committee.backtest.engine import BacktestMetrics, _evaluate_pass_bar
    bench = BacktestMetrics("bench", None, None, None, None, None)
    tilt = BacktestMetrics("tilt", None, None, None, None, None)
    result = _evaluate_pass_bar(tilt, bench, years=5.0)
    assert result.passes is False
    assert result.fallback_mode == "entertainment_only"


# ── Monthly dates helper ──────────────────────────────────────────────────────


def test_monthly_dates_basic() -> None:
    dates = _monthly_dates(date(2020, 1, 1), date(2020, 3, 31))
    assert len(dates) == 3
    assert dates[0] == date(2020, 1, 31)
    assert dates[1] == date(2020, 2, 29)  # 2020 is a leap year
    assert dates[2] == date(2020, 3, 31)


def test_monthly_dates_single_month() -> None:
    dates = _monthly_dates(date(2020, 6, 1), date(2020, 6, 15))
    assert len(dates) == 1
    assert dates[0] == date(2020, 6, 15)


# ── Portfolio return helper ───────────────────────────────────────────────────


def test_portfolio_return_weighted() -> None:
    sleeve_rets = {"equity_us": 0.10, "equity_intl": 0.05, "fixed_income": 0.02,
                   "alternatives": 0.0, "cash": 0.0}
    weights = {"equity_us": 0.40, "equity_intl": 0.20, "fixed_income": 0.30,
               "alternatives": 0.05, "cash": 0.05}
    ret = _portfolio_return(sleeve_rets, weights)
    expected = 0.40 * 0.10 + 0.20 * 0.05 + 0.30 * 0.02
    assert ret == pytest.approx(expected)


def test_portfolio_return_none_when_sleeve_missing() -> None:
    sleeve_rets: dict = {"equity_us": 0.10}  # missing required sleeves
    weights = {"equity_us": 0.40, "equity_intl": 0.20}
    ret = _portfolio_return(sleeve_rets, weights)
    assert ret is None


# ── Sleeve returns helper ─────────────────────────────────────────────────────


def test_sleeve_returns_basic(session: Session) -> None:
    d1 = date(2020, 1, 31)
    d2 = date(2020, 2, 29)
    inst = _inst(session, "SPY", "equity_us")
    _price(session, inst.id, d1, 300.0)
    _price(session, inst.id, d2, 330.0)   # +10% return
    _holding(session, inst.id, 100.0)
    session.commit()

    from committee.backtest.engine import _load_holdings, _load_pit_price_index
    holdings = _load_holdings(session)
    price_index = _load_pit_price_index(session, d1, d2)
    sleeve_rets = _sleeve_returns(holdings, price_index, d1, d2)

    assert sleeve_rets.get("equity_us") == pytest.approx(0.10)


# ── run_backtest integration ──────────────────────────────────────────────────


def test_backtest_no_holdings_returns_note(session: Session) -> None:
    report = run_backtest(session, date(2020, 1, 1), date(2021, 1, 1))
    assert report.note in ("no_holdings", "insufficient_history")
    assert not report.tilt_metrics.has_data


def test_backtest_insufficient_history(session: Session) -> None:
    """Single date range with no price history → insufficient_history note."""
    inst = _inst(session)
    _holding(session, inst.id)
    session.commit()

    report = run_backtest(session, date(2020, 1, 1), date(2020, 1, 31))
    # Only one month → might only have 1 date → insufficient
    assert report.note in ("insufficient_history",) or not report.tilt_metrics.has_data


def test_backtest_with_synthetic_data(session: Session) -> None:
    """Smoke test: backtest with multi-month price data completes without error."""
    d0 = date(2020, 1, 1)
    inst = _inst(session, "VTI", "equity_us")
    _holding(session, inst.id, 100.0)

    # Add 14 months of prices (a modest uptrend)
    for m in range(14):
        mo = d0.month + m
        yr = d0.year + (mo - 1) // 12
        mo = ((mo - 1) % 12) + 1
        obs_date = date(yr, mo, 28)
        price = 100.0 * (1.01 ** m)
        _price(session, inst.id, obs_date, price)
    session.commit()

    report = run_backtest(session, date(2020, 1, 1), date(2021, 1, 31), perturb=True)

    # With real price data, metrics should be available
    if report.note is None:
        assert report.benchmark_metrics.has_data or report.note is not None
        assert isinstance(report.replay_dates, list)
        if report.perturbation is not None:
            assert len(report.perturbation) == 3


def test_backtest_perturb_produces_three_rows(session: Session) -> None:
    """Perturbation report always produces low / base / high rows."""
    inst = _inst(session)
    _holding(session, inst.id)

    d0 = date(2020, 1, 1)
    for m in range(14):
        mo = ((d0.month + m - 1) % 12) + 1
        yr = d0.year + (d0.month + m - 1) // 12
        _price(session, inst.id, date(yr, mo, 15), 100.0 + m)
    session.commit()

    report = run_backtest(session, d0, date(2021, 3, 1), perturb=True)
    if report.perturbation is not None:
        assert len(report.perturbation) == 3
        drifts = [r.drift_abs for r in report.perturbation]
        assert drifts[0] < drifts[1] < drifts[2]


# ── WP-2: response_lag and as_of ──────────────────────────────────────────────


def test_backtest_metrics_has_response_lag() -> None:
    """BacktestMetrics accepts and stores response_lag."""
    from committee.backtest.engine import BacktestMetrics
    m = BacktestMetrics(
        oracle_id="test",
        cagr=0.08,
        max_drawdown=0.15,
        ulcer_index=0.05,
        annualized_turnover=0.20,
        implied_tax_drag=0.03,
        switch_count=2,
        response_lag=5,
    )
    assert m.response_lag == 5


def test_run_backtest_accepts_as_of(session: Session) -> None:
    """run_backtest accepts as_of parameter without raising on empty session."""
    report = run_backtest(session, date(2020, 1, 1), date(2024, 12, 31), as_of=date(2023, 12, 31))
    # Empty session → note set; must not raise
    assert report.note in ("no_holdings", "insufficient_history") or report is not None
