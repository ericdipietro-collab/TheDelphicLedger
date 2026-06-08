"""Tests for market data modules (prices, FRED, EDGAR, freshness, persist).

All HTTP calls are mocked — no live network access.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


# ── Tiingo price parsing ───────────────────────────────────────────────────────

def test_tiingo_parse_adj_close() -> None:
    from committee.market.prices import TiingoAdapter

    raw = json.loads((FIXTURES / "tiingo_aapl_eod.json").read_text())

    class _FakeSession:
        def get(self, *a, **kw):
            class R:
                text = json.dumps(raw)
                def raise_for_status(self): pass
            return R()
        headers = {}
        def update(self, *a, **kw): pass

    adapter = TiingoAdapter.__new__(TiingoAdapter)
    adapter._session = _FakeSession()  # type: ignore[attr-defined]

    # Call the underlying method directly via the requests mock
    import responses as responses_lib

    @responses_lib.activate
    def _run():
        responses_lib.add(
            responses_lib.GET,
            "https://api.tiingo.com/tiingo/daily/AAPL/prices",
            json=raw,
        )
        real_adapter = TiingoAdapter("fake_key")
        result = real_adapter.fetch_eod("AAPL", date(2026, 6, 2), date(2026, 6, 4))
        assert len(result) == 3
        assert isinstance(result[0].adj_close, Decimal)
        assert result[0].adj_close == Decimal("189.30")
        assert result[1].dividend == Decimal("0.25")
        assert result[2].dividend == Decimal("0")

    _run()


def test_tiingo_decimal_not_float() -> None:
    """Adj_close values must be Decimal, never float."""
    import responses as responses_lib

    from committee.market.prices import TiingoAdapter

    raw = json.loads((FIXTURES / "tiingo_aapl_eod.json").read_text())

    @responses_lib.activate
    def _run():
        responses_lib.add(
            responses_lib.GET,
            "https://api.tiingo.com/tiingo/daily/AAPL/prices",
            json=raw,
        )
        adapter = TiingoAdapter("key")
        result = adapter.fetch_eod("AAPL", date(2026, 6, 1), date(2026, 6, 7))
        for obs in result:
            assert type(obs.adj_close) is Decimal, "adj_close must be Decimal"
            assert type(obs.dividend) is Decimal, "dividend must be Decimal"

    _run()


# ── FRED CSV parsing ───────────────────────────────────────────────────────────

def test_fred_parse_t10y3m() -> None:
    from committee.market.fred import _parse_fred_csv

    text = (FIXTURES / "fred_t10y3m.csv").read_text()
    obs = _parse_fred_csv("T10Y3M", text)
    # Row with "." should be skipped
    assert all(o.series_id == "T10Y3M" for o in obs)
    assert len(obs) == 11  # 12 rows minus 1 missing (".")
    assert isinstance(obs[0].value, Decimal)
    assert obs[0].value == Decimal("-0.45")


def test_fred_parse_skips_missing_values() -> None:
    from committee.market.fred import _parse_fred_csv

    text = "DATE,T10Y3M\n2026-01-01,.\n2026-01-02,1.23\n"
    obs = _parse_fred_csv("T10Y3M", text)
    assert len(obs) == 1
    assert obs[0].value == Decimal("1.23")


def test_fred_fetch_via_http_mock() -> None:
    import responses as responses_lib

    from committee.market.fred import fetch_fred_series

    text = (FIXTURES / "fred_t10y3m.csv").read_text()

    @responses_lib.activate
    def _run():
        responses_lib.add(
            responses_lib.GET,
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            body=text,
        )
        result = fetch_fred_series("T10Y3M")
        assert len(result) > 0
        assert result[0].series_id == "T10Y3M"

    _run()


def test_cpi_yoy_computation() -> None:
    from committee.market.fred import compute_cpi_yoy

    text = (FIXTURES / "fred_cpiaucsl.csv").read_text()
    from committee.market.fred import _parse_fred_csv
    raw_obs = _parse_fred_csv("CPIAUCSL", text)

    yoy_obs = compute_cpi_yoy(raw_obs)
    # Should have YoY for months where 12-month prior data exists
    assert len(yoy_obs) > 0
    assert all(o.series_id == "CPIAUCSL_YOY" for o in yoy_obs)
    assert all(o.unit == "pct" for o in yoy_obs)
    assert all(isinstance(o.value, Decimal) for o in yoy_obs)

    # Verify a specific YoY calculation:
    # 2026-03 = 316.900, 2025-03 = 310.326 → YoY = (316.9 - 310.326) / 310.326 * 100
    mar26 = next((o for o in yoy_obs if o.observed_date == date(2026, 3, 1)), None)
    assert mar26 is not None
    expected = (Decimal("316.900") - Decimal("310.326")) / Decimal("310.326") * Decimal("100")
    assert abs(mar26.value - expected.quantize(Decimal("0.0001"))) < Decimal("0.001")


def test_cpi_yoy_decimal_math() -> None:
    """YoY must be Decimal end-to-end (Invariant E)."""
    from committee.market.fred import FredObs, compute_cpi_yoy

    obs = [
        FredObs("CPIAUCSL", date(2025, 1, 1), Decimal("310"), "index"),
        FredObs("CPIAUCSL", date(2026, 1, 1), Decimal("320"), "index"),
    ]
    yoy = compute_cpi_yoy(obs)
    assert len(yoy) == 1
    assert type(yoy[0].value) is Decimal
    # (320 - 310) / 310 * 100 ≈ 3.2258
    assert abs(yoy[0].value - Decimal("3.2258")) < Decimal("0.001")


# ── EDGAR parsing ──────────────────────────────────────────────────────────────

def test_edgar_parse_companyfacts() -> None:
    from committee.market.edgar import _parse_companyfacts

    data = json.loads((FIXTURES / "edgar_aapl_facts.json").read_text())
    obs = _parse_companyfacts(data)
    assert len(obs) > 0
    metrics = {o.metric for o in obs}
    assert "revenue" in metrics
    assert "eps_diluted" in metrics
    assert "equity" in metrics
    assert "cfo" in metrics
    assert "capex" in metrics


def test_edgar_revenue_decimal() -> None:
    """Fundamental values must be Decimal (Invariant E)."""
    from committee.market.edgar import _parse_companyfacts

    data = json.loads((FIXTURES / "edgar_aapl_facts.json").read_text())
    obs = _parse_companyfacts(data)
    for o in obs:
        assert type(o.value) is Decimal, f"{o.metric}: value must be Decimal"


def test_edgar_revenue_value() -> None:
    from committee.market.edgar import _parse_companyfacts

    data = json.loads((FIXTURES / "edgar_aapl_facts.json").read_text())
    obs = _parse_companyfacts(data)
    revenue_2023 = next(
        (o for o in obs if o.metric == "revenue" and o.period_end == date(2023, 9, 30)),
        None,
    )
    assert revenue_2023 is not None
    assert revenue_2023.value == Decimal("383285000000")
    assert revenue_2023.filed_at == date(2023, 11, 3)


def test_edgar_only_annual_filings() -> None:
    """Only 10-K (annual) filings should be parsed."""
    from committee.market.edgar import _parse_companyfacts

    data = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"end": "2023-09-30", "val": 383285000000, "form": "10-K", "filed": "2023-11-03"},
                            {"end": "2023-06-30", "val": 95000000000, "form": "10-Q", "filed": "2023-08-01"},
                        ]
                    }
                }
            }
        }
    }
    obs = _parse_companyfacts(data)
    # Only 10-K row should appear
    assert len(obs) == 1
    assert obs[0].period_end == date(2023, 9, 30)


def test_edgar_parse_8k_flags() -> None:
    from committee.market.edgar import _parse_8k_flags

    data = json.loads((FIXTURES / "edgar_aapl_submissions.json").read_text())
    flags = _parse_8k_flags(data)
    # First 8-K has item 2.02 (not tracked), second 8-K has 5.02 (tracked)
    assert len(flags) == 1
    assert flags[0].item_code == "5.02"
    assert flags[0].filing_date == date(2024, 3, 10)


def test_edgar_cik_lookup() -> None:
    import responses as responses_lib

    from committee.market.edgar import clear_cik_cache, lookup_cik

    fixture = (FIXTURES / "edgar_cik_map.json").read_text()

    @responses_lib.activate
    def _run():
        clear_cik_cache()
        responses_lib.add(
            responses_lib.GET,
            "https://www.sec.gov/files/company_tickers.json",
            body=fixture,
        )
        cik = lookup_cik("AAPL")
        assert cik == 320193

    _run()


def test_edgar_cik_unknown_ticker() -> None:
    import responses as responses_lib

    from committee.market.edgar import clear_cik_cache, lookup_cik

    fixture = (FIXTURES / "edgar_cik_map.json").read_text()

    @responses_lib.activate
    def _run():
        clear_cik_cache()
        responses_lib.add(
            responses_lib.GET,
            "https://www.sec.gov/files/company_tickers.json",
            body=fixture,
        )
        cik = lookup_cik("ZZZUNKNOWN")
        assert cik is None

    _run()


# ── Freshness SLA ──────────────────────────────────────────────────────────────

def test_freshness_within_sla() -> None:
    from committee.market.freshness import is_fresh

    recent = datetime.now(UTC)
    assert is_fresh(recent, "tiingo") is True
    assert is_fresh(recent, "fred") is True
    assert is_fresh(recent, "edgar_xbrl") is True


def test_freshness_exceeded_sla() -> None:
    from datetime import timedelta

    from committee.market.freshness import is_fresh

    old = datetime.now(UTC) - timedelta(days=2)
    assert is_fresh(old, "tiingo") is False  # SLA = 1 day

    old_fred = datetime.now(UTC) - timedelta(days=8)
    assert is_fresh(old_fred, "fred") is False  # SLA = 7 days


def test_freshness_none_returns_false() -> None:
    from committee.market.freshness import is_fresh

    assert is_fresh(None, "tiingo") is False
    assert is_fresh(None, "unknown") is False


# ── Persistence helpers ────────────────────────────────────────────────────────

def test_save_price_observations_no_duplicates(db_session) -> None:
    from committee.market.persist import save_price_observations

    obs = [
        (date(2026, 6, 1), Decimal("189.30"), Decimal("0")),
        (date(2026, 6, 2), Decimal("191.00"), Decimal("0.25")),
    ]
    n1 = save_price_observations(db_session, "tiingo", "AAPL", None, obs)
    n2 = save_price_observations(db_session, "tiingo", "AAPL", None, obs)
    assert n1 == 2
    assert n2 == 0  # already exists, no duplicates


def test_save_fred_observations_no_duplicates(db_session) -> None:
    from committee.market.persist import save_fred_observations

    obs = [
        (date(2026, 6, 1), Decimal("-0.45"), "pct"),
        (date(2026, 6, 2), Decimal("-0.43"), "pct"),
    ]
    n1 = save_fred_observations(db_session, "T10Y3M", obs)
    n2 = save_fred_observations(db_session, "T10Y3M", obs)
    assert n1 == 2
    assert n2 == 0


def test_save_fundamentals_no_duplicates(db_session) -> None:
    from committee.market.persist import save_fundamentals
    from committee.models import Instrument

    inst = Instrument(
        ticker="AAPL", name="Apple", instrument_type="stock",
        asset_class="equity", sleeve="unclassified",
        is_cash_equivalent=False, needs_unwind=True, aliases=[], bundle_tags=[],
    )
    db_session.add(inst)
    db_session.flush()

    obs = [(date(2023, 9, 30), date(2023, 11, 3), "revenue", Decimal("383285000000"), "USD")]
    n1 = save_fundamentals(db_session, inst.id, obs)
    n2 = save_fundamentals(db_session, inst.id, obs)
    assert n1 == 1
    assert n2 == 0
