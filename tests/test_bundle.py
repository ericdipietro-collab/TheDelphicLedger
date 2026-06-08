"""Tests for bundle loading, management, and index_proxy refresh."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

FIXTURES = Path(__file__).parent / "fixtures"
BUNDLES_DIR = Path(__file__).parent.parent / "bundles"
PROFILES_DIR = Path(__file__).parent.parent / "profiles"


# ── Bundle config loading ──────────────────────────────────────────────────────

def test_load_etf_core_bundle() -> None:
    from committee.universe.loader import load_bundle_config

    bundle = load_bundle_config("etf_core", BUNDLES_DIR)
    assert bundle is not None
    assert bundle.id == "etf_core"
    assert bundle.source == "explicit"
    assert len(bundle.tickers) >= 70
    assert bundle.refresh_policy == "nightly"
    assert bundle.enabled_default is True


def test_load_sp500_bundle() -> None:
    from committee.universe.loader import load_bundle_config

    bundle = load_bundle_config("sp500", BUNDLES_DIR)
    assert bundle is not None
    assert bundle.source == "index_proxy"
    assert bundle.index_proxy is not None
    assert bundle.index_proxy.template == "iShares Holdings"
    assert bundle.enabled_default is False


def test_load_nasdaq100_not_full_composite() -> None:
    """Ensure nasdaq100 bundle references QQQ (~100 names), not the full Composite."""
    from committee.universe.loader import load_bundle_config

    bundle = load_bundle_config("nasdaq100", BUNDLES_DIR)
    assert bundle is not None
    assert bundle.size_estimate is not None
    assert bundle.size_estimate < 200  # QQQ ~100; full Composite ~3400


def test_load_russell2000_on_demand() -> None:
    """Russell 2000 should be on_demand refresh to avoid EDGAR rate exhaustion."""
    from committee.universe.loader import load_bundle_config

    bundle = load_bundle_config("russell2000", BUNDLES_DIR)
    assert bundle is not None
    assert bundle.refresh_policy == "on_demand"
    assert bundle.enabled_default is False


def test_load_all_bundles() -> None:
    from committee.universe.loader import load_bundle_configs

    configs = load_bundle_configs(BUNDLES_DIR)
    ids = {c.id for c in configs}
    assert {"etf_core", "sp500", "dow30", "nasdaq100", "russell2000", "watchlist"}.issubset(ids)


def test_ticker_entry_plain_string(tmp_path: Path) -> None:
    """Plain string tickers in YAML are normalised to TickerEntry objects."""
    yaml_content = """
id: test_bundle
description: Test
source: explicit
tickers:
  - SPY
  - AGG
"""
    bundle_file = tmp_path / "test_bundle.yaml"
    bundle_file.write_text(yaml_content)
    from committee.universe.loader import load_bundle_configs
    configs = load_bundle_configs(tmp_path)
    assert len(configs) == 1
    assert configs[0].tickers[0].ticker == "SPY"
    assert configs[0].tickers[1].ticker == "AGG"


def test_ticker_entry_with_sleeve(tmp_path: Path) -> None:
    yaml_content = """
id: watchlist_test
description: Test
source: explicit
tickers:
  - ticker: AAPL
    sleeve: equity_us
"""
    (tmp_path / "w.yaml").write_text(yaml_content)
    from committee.universe.loader import load_bundle_configs
    configs = load_bundle_configs(tmp_path)
    assert configs[0].tickers[0].sleeve == "equity_us"


# ── Enable / Disable ──────────────────────────────────────────────────────────

def test_enable_bundle(db_session: Session) -> None:
    from committee.models import BundleState
    from committee.universe.loader import load_bundle_config
    from committee.universe.manager import enable_bundle

    bundle = load_bundle_config("etf_core", BUNDLES_DIR)
    assert bundle is not None
    status, msg = enable_bundle(db_session, bundle)
    assert status in ("ok", "warn")
    state = db_session.get(BundleState, "etf_core")
    assert state is not None
    assert state.enabled


def test_disable_bundle(db_session: Session) -> None:
    from committee.models import BundleState
    from committee.universe.manager import disable_bundle

    state = BundleState(id="test_bundle", enabled=True, instrument_count=0)
    db_session.add(state)
    db_session.flush()

    msg = disable_bundle(db_session, "test_bundle")
    assert "disabled" in msg
    assert not db_session.get(BundleState, "test_bundle").enabled


def test_disable_nonexistent_bundle(db_session: Session) -> None:
    from committee.universe.manager import disable_bundle

    msg = disable_bundle(db_session, "nonexistent_bundle")
    assert "not found" in msg


# ── Explicit bundle refresh ───────────────────────────────────────────────────

def test_refresh_explicit_bundle_tags_instruments(db_session: Session, tmp_path: Path) -> None:
    """Refreshing an explicit bundle resolves tickers and tags instruments."""
    from committee.models import Instrument
    from committee.universe.bundle import BundleConfig, TickerEntry
    from committee.universe.manager import refresh_bundle

    bundle = BundleConfig(
        id="test_explicit",
        description="Test",
        source="explicit",
        refresh_policy="nightly",
        tickers=[TickerEntry(ticker="AAPL"), TickerEntry(ticker="MSFT")],
    )
    count, err = refresh_bundle(db_session, bundle, tmp_path)
    assert err is None
    assert count == 2

    instruments = db_session.query(Instrument).all()
    tickers = {i.ticker for i in instruments}
    assert {"AAPL", "MSFT"}.issubset(tickers)

    # All tagged instruments should have the bundle id in their bundle_tags
    for inst in instruments:
        if inst.ticker in ("AAPL", "MSFT"):
            assert "test_explicit" in (inst.bundle_tags or [])


def test_refresh_explicit_bundle_idempotent(db_session: Session, tmp_path: Path) -> None:
    """Second refresh should not create duplicate tag entries."""
    from committee.universe.bundle import BundleConfig, TickerEntry
    from committee.universe.manager import refresh_bundle

    bundle = BundleConfig(
        id="test_idem",
        description="Test",
        source="explicit",
        tickers=[TickerEntry(ticker="SPY")],
    )
    refresh_bundle(db_session, bundle, tmp_path)
    count2, _ = refresh_bundle(db_session, bundle, tmp_path)
    # Second refresh: SPY already tagged, count = 0 (no new tags)
    assert count2 == 0


def test_refresh_sets_bundle_state(db_session: Session, tmp_path: Path) -> None:
    from committee.models import BundleState
    from committee.universe.bundle import BundleConfig, TickerEntry
    from committee.universe.manager import refresh_bundle

    bundle = BundleConfig(
        id="test_state",
        description="Test",
        source="explicit",
        tickers=[TickerEntry(ticker="VTI"), TickerEntry(ticker="BND")],
    )
    refresh_bundle(db_session, bundle, tmp_path)
    state = db_session.get(BundleState, "test_state")
    assert state is not None
    assert state.last_refreshed_at is not None
    assert state.instrument_count == 2
    assert state.last_error is None


# ── Index proxy bundle refresh ────────────────────────────────────────────────

def test_refresh_index_proxy_bundle(db_session: Session) -> None:
    """index_proxy refresh: injectable http_get parses iShares fixture CSV."""
    from committee.models import Instrument
    from committee.universe.bundle import BundleConfig, IndexProxyConfig
    from committee.universe.manager import refresh_bundle

    csv_bytes = (FIXTURES / "ishares_sample_holdings.csv").read_bytes()

    def fake_http_get(url: str) -> bytes:
        return csv_bytes

    bundle = BundleConfig(
        id="sp500_test",
        description="Test S&P 500",
        source="index_proxy",
        index_proxy=IndexProxyConfig(url="http://fake.url", template="iShares Holdings"),
    )
    count, err = refresh_bundle(db_session, bundle, PROFILES_DIR, http_get=fake_http_get)
    assert err is None, f"Unexpected error: {err}"
    assert count > 0

    instruments = db_session.query(Instrument).all()
    tickers = {i.ticker for i in instruments}
    # iShares fixture has AAPL, MSFT, NVDA, AMZN, GOOGL
    assert "AAPL" in tickers
    assert "MSFT" in tickers

    for inst in instruments:
        assert "sp500_test" in (inst.bundle_tags or [])


def test_refresh_index_proxy_missing_template(db_session: Session, tmp_path: Path) -> None:
    """Missing mapping template → error returned, no crash."""
    from committee.universe.bundle import BundleConfig, IndexProxyConfig
    from committee.universe.manager import refresh_bundle

    bundle = BundleConfig(
        id="bad_proxy",
        description="Bad",
        source="index_proxy",
        index_proxy=IndexProxyConfig(url="http://x", template="Nonexistent Template"),
    )
    count, err = refresh_bundle(db_session, bundle, tmp_path, http_get=lambda u: b"")
    assert count == 0
    assert err is not None
    assert "not found" in err.lower() or "nonexistent" in err.lower()
