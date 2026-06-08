"""Tests for the M3 reconciliation engine.

Four required scenarios:
1. missed_reinvest  — extra shares in T1 with a dividend observation in window
2. unrecorded_transfer — unexplained delta; account has other txns (not a coverage gap)
3. split — recorded InstrumentEvent; projector closes cleanly, no break
4. coverage_gap — no account activity in (T0, T1]; unverifiable delta
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from committee.models import (
    ImportBatch,
    Instrument,
    InstrumentEvent,
    MarketObservation,
    PositionSnapshot,
    ReconBreak,
    Transaction,
)
from committee.recon.engine import resolve_break, run_recon
from committee.recon.projector import project_qty

# ── Fixtures helpers ──────────────────────────────────────────────────────────

def _inst(session: Session, ticker: str) -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=f"{ticker} Inc",
        instrument_type="stock",
        asset_class="equity",
        sleeve="unclassified",
        is_cash_equivalent=False,
        needs_unwind=False,
        aliases=[],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


def _batch(session: Session) -> ImportBatch:
    batch = ImportBatch(
        file_hash=str(uuid.uuid4()),
        original_filename="test.csv",
        file_type="positions",
        row_count=1,
    )
    session.add(batch)
    session.flush()
    return batch


def _snap(
    session: Session,
    inst: Instrument,
    batch: ImportBatch,
    account: str,
    as_of: date,
    qty: Decimal,
) -> PositionSnapshot:
    snap = PositionSnapshot(
        batch_id=batch.id,
        account_id=account,
        instrument_id=inst.id,
        raw_instrument=inst.ticker,
        as_of=as_of,
        qty=qty,
    )
    session.add(snap)
    session.flush()
    return snap


def _txn(
    session: Session,
    inst: Instrument,
    batch: ImportBatch,
    account: str,
    trade_date: date,
    canonical_type: str,
    qty: Decimal,
) -> Transaction:
    txn = Transaction(
        batch_id=batch.id,
        account_id=account,
        instrument_id=inst.id,
        raw_instrument=inst.ticker,
        trade_date=trade_date,
        raw_type=canonical_type,
        canonical_type=canonical_type,
        qty=qty,
    )
    session.add(txn)
    session.flush()
    return txn


# ── Scenario 1: Missed reinvestment ──────────────────────────────────────────

def test_missed_reinvest_opens_break(db_session: Session) -> None:
    """T1 shows extra shares matching a dividend in the window; no reinvest txn recorded.

    Account has another transaction (SPY buy) in the window → not a coverage gap.
    Expected: open break, suggested_cause = "missed_reinvest".
    """
    aapl = _inst(db_session, "AAPL")
    spy = _inst(db_session, "SPY")
    batch = _batch(db_session)
    acct = "acct1"

    _snap(db_session, aapl, batch, acct, date(2026, 1, 1), Decimal("100"))
    _snap(db_session, aapl, batch, acct, date(2026, 1, 31), Decimal("102"))

    # Account coverage provided by SPY buy
    _txn(db_session, spy, batch, acct, date(2026, 1, 15), "buy", Decimal("10"))

    # Dividend observation for AAPL in the window
    db_session.add(
        MarketObservation(
            source="tiingo",
            series_id="AAPL",
            instrument_id=aapl.id,
            observed_date=date(2026, 1, 15),
            value=Decimal("0.25"),
            unit="USD_dividend",
            degraded=False,
        )
    )
    db_session.flush()

    summary = run_recon(db_session)

    brk = db_session.query(ReconBreak).filter_by(
        instrument_id=aapl.id, account_id=acct
    ).one()

    assert brk.status == "open"
    assert brk.delta == Decimal("2")
    assert brk.suggested_cause == "missed_reinvest"
    assert not brk.coverage_gap
    assert summary.open_breaks == 1
    assert summary.coverage_gaps == 0


# ── Scenario 2: Unrecorded transfer ──────────────────────────────────────────

def test_unrecorded_transfer_opens_break(db_session: Session) -> None:
    """MSFT drops 50 shares with no MSFT transaction; account has SPY activity (covered).

    Expected: open break, suggested_cause = "unrecorded_transfer".
    """
    msft = _inst(db_session, "MSFT")
    spy = _inst(db_session, "SPY")
    batch = _batch(db_session)
    acct = "acct2"

    _snap(db_session, msft, batch, acct, date(2026, 2, 1), Decimal("200"))
    _snap(db_session, msft, batch, acct, date(2026, 2, 28), Decimal("150"))

    # Account coverage via SPY buy — MSFT itself has no transactions
    _txn(db_session, spy, batch, acct, date(2026, 2, 15), "buy", Decimal("20"))
    db_session.flush()

    summary = run_recon(db_session)

    brk = db_session.query(ReconBreak).filter_by(
        instrument_id=msft.id, account_id=acct
    ).one()

    assert brk.status == "open"
    assert brk.delta == Decimal("-50")  # actual(150) - expected(200) = -50
    assert brk.suggested_cause == "unrecorded_transfer"
    assert not brk.coverage_gap
    assert summary.open_breaks == 1


# ── Scenario 3: Split via InstrumentEvent ─────────────────────────────────────

def test_recorded_split_projects_to_clean_checkpoint(db_session: Session) -> None:
    """A 20:1 split recorded in instrument_events; projector closes at 1000 shares.

    T0: 50 GOOGL, split 20:1 → expected 1000, T1 = 1000 → delta = 0 → no break.
    """
    googl = _inst(db_session, "GOOGL")
    batch = _batch(db_session)
    acct = "acct3"

    _snap(db_session, googl, batch, acct, date(2026, 3, 1), Decimal("50"))
    _snap(db_session, googl, batch, acct, date(2026, 3, 31), Decimal("1000"))

    db_session.add(
        InstrumentEvent(
            instrument_id=googl.id,
            event_date=date(2026, 3, 10),
            event_type="split",
            ratio=Decimal("20"),
        )
    )
    db_session.flush()

    # Verify the projector directly
    result = project_qty(db_session, googl.id, acct, date(2026, 3, 1), Decimal("50"), date(2026, 3, 31))
    assert result.expected_qty == Decimal("1000")
    assert result.coverage_gap is False  # split event counts as coverage

    summary = run_recon(db_session)

    breaks = db_session.query(ReconBreak).filter_by(
        instrument_id=googl.id, account_id=acct
    ).all()
    assert breaks == []
    assert summary.checkpoints_clean == 1
    assert summary.open_breaks == 0


def test_unrecorded_split_suggests_split_cause(db_session: Session) -> None:
    """A split that wasn't recorded: actual_qty ≈ 20 × t0_qty → cause = 'split'."""
    from committee.recon.explainer import SPLIT, suggest_cause

    googl = _inst(db_session, "GOOGL")
    db_session.flush()

    cause = suggest_cause(
        session=db_session,
        instrument_id=googl.id,
        account_id="acct3b",
        t0_date=date(2026, 3, 1),
        t1_date=date(2026, 3, 31),
        t0_qty=Decimal("50"),
        expected_qty=Decimal("50"),
        actual_qty=Decimal("1000"),
        delta=Decimal("950"),
        coverage_gap=False,
    )
    assert cause == SPLIT


# ── Scenario 4: Coverage gap ──────────────────────────────────────────────────

def test_coverage_gap_marks_break(db_session: Session) -> None:
    """No account activity in (T0, T1] → coverage_gap=True, cause='import_gap'."""
    nvda = _inst(db_session, "NVDA")
    batch = _batch(db_session)
    acct = "acct4"

    _snap(db_session, nvda, batch, acct, date(2026, 4, 1), Decimal("75"))
    _snap(db_session, nvda, batch, acct, date(2026, 4, 30), Decimal("80"))

    # Deliberately NO transactions or events in the window
    db_session.flush()

    summary = run_recon(db_session)

    brk = db_session.query(ReconBreak).filter_by(
        instrument_id=nvda.id, account_id=acct
    ).one()

    assert brk.coverage_gap
    assert brk.suggested_cause == "import_gap"
    assert brk.status == "open"
    assert summary.coverage_gaps == 1
    assert summary.open_breaks == 0


# ── Materiality ───────────────────────────────────────────────────────────────

def test_immaterial_delta_auto_closes(db_session: Session) -> None:
    """Delta < 0.5 shares is auto_closed, not left open."""
    brk_inst = _inst(db_session, "BRK")
    cover_inst = _inst(db_session, "DUST")
    batch = _batch(db_session)
    acct = "acct5"

    _snap(db_session, brk_inst, batch, acct, date(2026, 5, 1), Decimal("100"))
    _snap(db_session, brk_inst, batch, acct, date(2026, 5, 31), Decimal("100.3"))
    _txn(db_session, cover_inst, batch, acct, date(2026, 5, 15), "buy", Decimal("5"))
    db_session.flush()

    summary = run_recon(db_session)

    brk = db_session.query(ReconBreak).filter_by(
        instrument_id=brk_inst.id, account_id=acct
    ).one()
    assert brk.status == "auto_closed"
    assert brk.suggested_cause == "immaterial"
    assert summary.auto_closed == 1
    assert summary.open_breaks == 0


def test_materiality_pct_threshold(db_session: Session) -> None:
    """Delta at exactly 0.05% of a 10000-share position is immaterial (< 0.1%)."""
    big_inst = _inst(db_session, "BIG")
    cover_inst = _inst(db_session, "SMALL")
    batch = _batch(db_session)
    acct = "acct6"

    # 10000 shares; 0.05% = 5 shares delta → below 0.1% threshold
    _snap(db_session, big_inst, batch, acct, date(2026, 5, 1), Decimal("10000"))
    _snap(db_session, big_inst, batch, acct, date(2026, 5, 31), Decimal("10005"))
    _txn(db_session, cover_inst, batch, acct, date(2026, 5, 15), "buy", Decimal("1"))
    db_session.flush()

    summary = run_recon(db_session)

    brk = db_session.query(ReconBreak).filter_by(
        instrument_id=big_inst.id, account_id=acct
    ).one()
    assert brk.status == "auto_closed"
    assert summary.auto_closed == 1


# ── Projector unit tests ──────────────────────────────────────────────────────

def test_projector_buy_adds_qty(db_session: Session) -> None:
    inst = _inst(db_session, "TSLA")
    batch = _batch(db_session)
    _txn(db_session, inst, batch, "acct", date(2026, 1, 10), "buy", Decimal("15"))
    db_session.flush()

    result = project_qty(db_session, inst.id, "acct", date(2026, 1, 1), Decimal("100"), date(2026, 1, 31))
    assert result.expected_qty == Decimal("115")
    assert result.coverage_gap is False


def test_projector_sell_subtracts_qty(db_session: Session) -> None:
    inst = _inst(db_session, "AMZN")
    batch = _batch(db_session)
    _txn(db_session, inst, batch, "acct", date(2026, 1, 10), "sell", Decimal("20"))
    db_session.flush()

    result = project_qty(db_session, inst.id, "acct", date(2026, 1, 1), Decimal("100"), date(2026, 1, 31))
    assert result.expected_qty == Decimal("80")


def test_projector_split_interleaves_with_buy(db_session: Session) -> None:
    """Buy before split: 100 + buy 10 = 110; then 2:1 split = 220."""
    inst = _inst(db_session, "META")
    batch = _batch(db_session)
    acct = "acct"
    # Buy on day 5
    _txn(db_session, inst, batch, acct, date(2026, 1, 5), "buy", Decimal("10"))
    # Split on day 10
    db_session.add(
        InstrumentEvent(
            instrument_id=inst.id,
            event_date=date(2026, 1, 10),
            event_type="split",
            ratio=Decimal("2"),
        )
    )
    db_session.flush()

    result = project_qty(db_session, inst.id, acct, date(2026, 1, 1), Decimal("100"), date(2026, 1, 31))
    assert result.expected_qty == Decimal("220")


def test_projector_no_txns_no_events_is_coverage_gap(db_session: Session) -> None:
    inst = _inst(db_session, "QUIET")
    db_session.flush()

    result = project_qty(db_session, inst.id, "acct", date(2026, 1, 1), Decimal("50"), date(2026, 1, 31))
    assert result.expected_qty == Decimal("50")
    assert result.coverage_gap is True


# ── Resolve audit ─────────────────────────────────────────────────────────────

def test_resolve_break_writes_audit(db_session: Session) -> None:
    inst = _inst(db_session, "XOM")
    brk = ReconBreak(
        instrument_id=inst.id,
        account_id="acct7",
        as_of=date(2026, 6, 1),
        expected_qty=Decimal("100"),
        actual_qty=Decimal("105"),
        delta=Decimal("5"),
        status="open",
        coverage_gap=False,
        suggested_cause="missed_reinvest",
        opened_at=datetime.now(),
    )
    db_session.add(brk)
    db_session.flush()

    resolve_break(db_session, brk.id, "Confirmed DRIP reinvestment on 2026-01-15 not imported")

    db_session.flush()
    db_session.refresh(brk)
    assert brk.status == "resolved"
    assert "DRIP" in brk.resolution_note
    assert brk.resolved_at is not None


def test_resolve_already_resolved_raises(db_session: Session) -> None:
    import pytest

    inst = _inst(db_session, "CVX")
    brk = ReconBreak(
        instrument_id=inst.id,
        account_id="acct8",
        as_of=date(2026, 6, 1),
        expected_qty=Decimal("50"),
        actual_qty=Decimal("55"),
        delta=Decimal("5"),
        status="resolved",
        coverage_gap=False,
        opened_at=datetime.now(),
        resolved_at=datetime.now(),
    )
    db_session.add(brk)
    db_session.flush()

    with pytest.raises(ValueError, match="already resolved"):
        resolve_break(db_session, brk.id, "trying again")


def test_resolve_nonexistent_raises(db_session: Session) -> None:
    import pytest

    with pytest.raises(ValueError, match="not found"):
        resolve_break(db_session, 99999, "ghost break")


# ── Run recon is idempotent (preserves resolved breaks) ──────────────────────

def test_run_recon_preserves_resolved_breaks(db_session: Session) -> None:
    """Second run_recon does not wipe already-resolved breaks."""
    inst = _inst(db_session, "WMT")
    batch = _batch(db_session)
    acct = "acct9"

    _snap(db_session, inst, batch, acct, date(2026, 1, 1), Decimal("100"))
    _snap(db_session, inst, batch, acct, date(2026, 1, 31), Decimal("110"))
    db_session.flush()

    run_recon(db_session)

    brk = db_session.query(ReconBreak).filter_by(
        instrument_id=inst.id, account_id=acct
    ).one()
    resolve_break(db_session, brk.id, "Explained: manually verified")

    # Second run must not delete the resolved break
    run_recon(db_session)

    resolved = (
        db_session.query(ReconBreak)
        .filter_by(instrument_id=inst.id, account_id=acct, status="resolved")
        .all()
    )
    assert len(resolved) == 1
    assert "manually verified" in resolved[0].resolution_note
