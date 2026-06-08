"""Tests for the scrape guard (instrument cap enforcement)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from committee.models import BundleState, Holding, Instrument


def _make_inst(session: Session, ticker: str, bundle_tags: list[str] | None = None) -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=f"{ticker} Corp",
        instrument_type="etf",
        asset_class="equity",
        sleeve="unclassified",
        is_cash_equivalent=False,
        needs_unwind=False,
        aliases=[],
        bundle_tags=bundle_tags or [],
    )
    session.add(inst)
    session.flush()
    return inst


def _make_holding(session: Session, inst: Instrument) -> Holding:
    h = Holding(
        instrument_id=inst.id,
        account_id="acct1",
        as_of=date(2026, 6, 1),
        qty=Decimal("100"),
        market_value=Decimal("10000"),
    )
    session.add(h)
    session.flush()
    return h


# ── active_universe_size ───────────────────────────────────────────────────────

def test_universe_size_empty(db_session: Session) -> None:
    from committee.universe.guard import active_universe_size
    assert active_universe_size(db_session) == 0


def test_universe_size_holdings_only(db_session: Session) -> None:
    from committee.universe.guard import active_universe_size
    inst = _make_inst(db_session, "SPY")
    _make_holding(db_session, inst)
    assert active_universe_size(db_session) == 1


def test_universe_size_enabled_bundle(db_session: Session) -> None:
    from committee.universe.guard import active_universe_size
    # Enable bundle "etf_core"
    state = BundleState(id="etf_core", enabled=True, instrument_count=0)
    db_session.add(state)
    # Create 3 instruments tagged with etf_core
    for t in ("SPY", "AGG", "TLT"):
        _make_inst(db_session, t, bundle_tags=["etf_core"])
    db_session.flush()
    assert active_universe_size(db_session) == 3


def test_universe_size_disabled_bundle_not_counted(db_session: Session) -> None:
    from committee.universe.guard import active_universe_size
    # Disabled bundle instruments shouldn't count
    state = BundleState(id="sp500", enabled=False, instrument_count=0)
    db_session.add(state)
    _make_inst(db_session, "AAPL", bundle_tags=["sp500"])
    db_session.flush()
    assert active_universe_size(db_session) == 0


def test_universe_size_holdings_union_bundle(db_session: Session) -> None:
    """Holdings and bundle instruments are unioned (no double-counting)."""
    from committee.universe.guard import active_universe_size
    state = BundleState(id="etf_core", enabled=True, instrument_count=0)
    db_session.add(state)
    # SPY is in both holdings AND etf_core bundle
    spy = _make_inst(db_session, "SPY", bundle_tags=["etf_core"])
    _make_holding(db_session, spy)
    # AGG is only in bundle
    _make_inst(db_session, "AGG", bundle_tags=["etf_core"])
    db_session.flush()
    assert active_universe_size(db_session) == 2  # not 3


# ── check_guard ────────────────────────────────────────────────────────────────

def test_guard_ok(db_session: Session) -> None:
    from committee.universe.guard import check_guard
    status, projected = check_guard(db_session, new_count=50, cap=3000)
    assert status == "ok"
    assert projected == 50


def test_guard_warn_at_80_percent(db_session: Session) -> None:
    from committee.universe.guard import check_guard
    # Place current universe at 2300 (77%) and add 200 → 2500 (83%, >80%)
    state = BundleState(id="big_bundle", enabled=True, instrument_count=0)
    db_session.add(state)
    for i in range(2300):
        _make_inst(db_session, f"T{i:04d}", bundle_tags=["big_bundle"])
    db_session.flush()
    status, projected = check_guard(db_session, new_count=200, cap=3000)
    assert status == "warn"
    assert projected == 2500


def test_guard_refuse_at_cap(db_session: Session) -> None:
    from committee.universe.guard import check_guard
    # Place current universe at 2900 and add 200 → 3100 (over cap)
    state = BundleState(id="heavy", enabled=True, instrument_count=0)
    db_session.add(state)
    for i in range(2900):
        _make_inst(db_session, f"H{i:04d}", bundle_tags=["heavy"])
    db_session.flush()
    status, projected = check_guard(db_session, new_count=200, cap=3000)
    assert status == "refuse"
    assert projected == 3100


def test_enable_refuses_over_cap(db_session: Session) -> None:
    """enable_bundle must refuse when projected size exceeds cap."""
    from committee.universe.bundle import BundleConfig
    from committee.universe.manager import enable_bundle

    # Fill up to just below cap with holdings
    state = BundleState(id="existing", enabled=True, instrument_count=0)
    db_session.add(state)
    for i in range(2950):
        _make_inst(db_session, f"X{i:04d}", bundle_tags=["existing"])
    db_session.flush()

    # A bundle with 100 instruments would push to 3050 (over 3000)
    bundle = BundleConfig(
        id="too_big",
        description="Too big",
        source="explicit",
        size_estimate=100,
        tickers=[],
    )
    status, msg = enable_bundle(db_session, bundle, cap=3000)
    assert status == "refuse"
    assert "disable a bundle" in msg.lower()


def test_enable_warns_near_cap(db_session: Session) -> None:
    """enable_bundle warns when projected size is between 80% and 100% of cap."""
    from committee.universe.bundle import BundleConfig
    from committee.universe.manager import enable_bundle

    # Fill to 2300 instruments
    state = BundleState(id="base", enabled=True, instrument_count=0)
    db_session.add(state)
    for i in range(2300):
        _make_inst(db_session, f"W{i:04d}", bundle_tags=["base"])
    db_session.flush()

    # A bundle with 200 instruments → 2500 (>2400 warn threshold)
    bundle = BundleConfig(
        id="warn_bundle",
        description="Warn",
        source="explicit",
        size_estimate=200,
        tickers=[],
    )
    status, msg = enable_bundle(db_session, bundle, cap=3000)
    assert status == "ok"  # accepted but with warning
    assert "2500" in msg  # projected size mentioned
