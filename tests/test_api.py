"""Tests for the FastAPI dashboard API."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from committee.models import (
    Base,
    Decision,
    Holding,
    ImportBatch,
    Instrument,
    ReconBreak,
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test_api.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    return path


@pytest.fixture
def client(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import committee.api.deps as deps

    deps._engine = None  # reset any cached engine
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    deps._engine = engine

    from committee.api.app import app
    return TestClient(app)


def _add_instrument(session: Session, ticker: str, sleeve: str = "equity_us") -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=ticker + " Corp",
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


def _add_holding(session: Session, inst: Instrument, mv: float, acct: str = "taxable1") -> None:
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
        account_id=acct,
        as_of=date.today(),
        qty=Decimal("100"),
        market_value=Decimal(str(mv)),
    ))


def _add_run(session: Session, iid: int, scenario_id: str | None = None) -> str:
    run_id = str(uuid.uuid4())
    for oid, name in [
        ("value_purist", "The Value Purist"),
        ("growth_visionary", "Growth Visionary"),
        ("yield_harvester", "Yield Harvester"),
        ("macro_tactician", "Macro Tactician"),
        ("quality_compounder", "The Quality Compounder"),
        ("passive_pragmatist", "Passive Pragmatist"),
    ]:
        session.add(Decision(
            run_id=run_id,
            oracle_name=name,
            persona_key=oid,
            inputs_json={"holdings_count": 1, "constraint": "unconstrained", "scenario": scenario_id},
            outputs_json={
                "scores": {str(iid): {"score": 0.5, "reasons": ["pe_ratio"]}},
                "sleeve_targets": {"equity_us": "0.40", "equity_intl": "0.20",
                                   "fixed_income": "0.30", "alternatives": "0.05", "cash": "0.05"},
                "abstained": False,
            },
            proposals_json=[{
                "instrument_id": iid,
                "account_id": "taxable1",
                "direction": "buy",
                "qty": "10",
                "estimated_value": "1000",
                "tags": ["drift"],
                "oracle_score": 0.5,
                "tax_note": None,
            }],
            regime_state="neutral",
            scenario_id=scenario_id,
            dry_run=False,
        ))
    session.commit()
    return run_id


# ── /api/runs ──────────────────────────────────────────────────────────────────

def test_list_runs_empty(client: TestClient) -> None:
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_list_runs_returns_summaries(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "AAPL")
        s.flush()
        _add_run(s, inst.id)

    r = client.get("/api/runs")
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["oracle_count"] == 6
    assert runs[0]["proposal_count"] == 6  # 1 proposal per oracle


def test_get_latest_chamber_no_run(client: TestClient) -> None:
    r = client.get("/api/runs/latest")
    assert r.status_code == 404


def test_get_latest_chamber(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "MSFT")
        s.flush()
        _add_run(s, inst.id)

    r = client.get("/api/runs/latest")
    assert r.status_code == 200
    data = r.json()
    assert "oracle_cards" in data
    assert len(data["oracle_cards"]) == 6
    assert "dissent_matrix" in data
    assert "rival_objections" in data


def test_chamber_decimal_as_string(client: TestClient, db_path: Path) -> None:
    """Sleeve targets in oracle cards must be strings, not floats."""
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "GOOG")
        s.flush()
        _add_run(s, inst.id)

    r = client.get("/api/runs/latest")
    cards = r.json()["oracle_cards"]
    for card in cards:
        for v in card["sleeve_targets"].values():
            assert isinstance(v, str), f"Expected str, got {type(v)}: {v}"


# ── /api/portfolio ─────────────────────────────────────────────────────────────

def test_portfolio_empty(client: TestClient) -> None:
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    data = r.json()
    assert data["total_market_value"] == "0"
    assert data["holdings"] == []


def test_portfolio_with_holdings(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "SPY", sleeve="equity_us")
        s.flush()
        _add_holding(s, inst, 50000)
        s.commit()

    r = client.get("/api/portfolio")
    assert r.status_code == 200
    data = r.json()
    assert float(data["total_market_value"]) == pytest.approx(50000)
    assert len(data["holdings"]) == 1
    assert data["holdings"][0]["ticker"] == "SPY"


def test_portfolio_market_value_is_string(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "AGG", sleeve="fixed_income")
        s.flush()
        _add_holding(s, inst, 30000)
        s.commit()

    r = client.get("/api/portfolio")
    data = r.json()
    assert isinstance(data["total_market_value"], str)


# ── /api/trades ────────────────────────────────────────────────────────────────

def test_trades_no_run(client: TestClient) -> None:
    r = client.get("/api/trades")
    assert r.status_code == 404


def test_trades_with_run(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "NVDA")
        s.flush()
        _add_run(s, inst.id)

    r = client.get("/api/trades")
    assert r.status_code == 200
    data = r.json()
    assert "oracle_proposals" in data
    assert len(data["oracle_proposals"]) == 6


# ── /api/recon ─────────────────────────────────────────────────────────────────

def test_recon_empty(client: TestClient) -> None:
    r = client.get("/api/recon")
    assert r.status_code == 200
    data = r.json()
    assert data["open_count"] == 0
    assert data["breaks"] == []


def test_recon_with_breaks(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "TSLA")
        s.flush()
        s.add(ReconBreak(
            instrument_id=inst.id,
            account_id="taxable1",
            as_of=date.today(),
            expected_qty=Decimal("100"),
            actual_qty=Decimal("90"),
            delta=Decimal("-10"),
            status="open",
            coverage_gap=False,
        ))
        s.commit()

    r = client.get("/api/recon")
    assert r.status_code == 200
    data = r.json()
    assert data["open_count"] == 1
    assert len(data["breaks"]) == 1
    assert data["breaks"][0]["ticker"] == "TSLA"


# ── /api/config ────────────────────────────────────────────────────────────────

def test_config_returns_defaults(client: TestClient) -> None:
    r = client.get("/api/config")
    assert r.status_code == 200
    data = r.json()
    assert data["drift_abs"] == pytest.approx(0.05)
    assert data["min_trade_usd"] == pytest.approx(200.0)
    assert "unconstrained" in data["available_profiles"]


# ── /api/scenarios ─────────────────────────────────────────────────────────────

def test_scenarios_list(client: TestClient) -> None:
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    packs = r.json()
    # The scenarios directory has 6 packs
    assert len(packs) >= 6
    pack_ids = [p["pack_id"] for p in packs]
    assert "gfc-2008" in pack_ids
    assert "covid-2020" in pack_ids


def test_scenario_no_run_404(client: TestClient) -> None:
    r = client.get("/api/scenarios/gfc-2008")
    assert r.status_code == 404


def test_scenario_with_run(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "VTI")
        s.flush()
        _add_holding(s, inst, 100000)
        _add_run(s, inst.id, scenario_id="gfc-2008")

    r = client.get("/api/scenarios/gfc-2008")
    assert r.status_code == 200
    data = r.json()
    assert data["pack_id"] == "gfc-2008"
    assert len(data["waterfall"]) == 5  # 5 sleeves
    assert len(data["verdict_deltas"]) == 6


def test_scenario_waterfall_decimal_strings(client: TestClient, db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    with Session(engine) as s:
        inst = _add_instrument(s, "BND", sleeve="fixed_income")
        s.flush()
        _add_holding(s, inst, 40000)
        _add_run(s, inst.id, scenario_id="rate-shock-300bps")

    r = client.get("/api/scenarios/rate-shock-300bps")
    assert r.status_code == 200
    for row in r.json()["waterfall"]:
        assert isinstance(row["before_mv"], str)
        assert isinstance(row["after_mv"], str)


# ── /api/data/setup-status ─────────────────────────────────────────────────────

def test_setup_status_includes_unresolved_count(client: TestClient) -> None:
    r = client.get("/api/data/setup-status")
    assert r.status_code == 200
    body = r.json()
    assert "unresolved_count" in body
    assert body["unresolved_count"] == 0  # empty DB has no unresolved items


def test_setup_status_includes_staleness_dates(client: TestClient) -> None:
    r = client.get("/api/data/setup-status")
    assert r.status_code == 200
    body = r.json()
    assert "prices_as_of" in body
    assert "edgar_as_of" in body
    # Empty DB → both are null
    assert body["prices_as_of"] is None
    assert body["edgar_as_of"] is None


# ── /api/data/fetch-progress ───────────────────────────────────────────────────

def test_fetch_progress_endpoint_idle(client: TestClient) -> None:
    r = client.get("/api/data/fetch-progress")
    assert r.status_code == 200
    body = r.json()
    assert body["operation"] is None
    assert body["current"] == 0
    assert body["total"] == 0
