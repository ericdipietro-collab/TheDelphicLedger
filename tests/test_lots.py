"""Tests for M6: tax lot derivation, ST/LT analysis, wash-sale detection, unwind ordering."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from committee.lots.analysis import (
    compute_unwind_analysis,
    days_to_long_term,
    estimated_tax,
    is_long_term,
    lot_gain,
    wash_sale_risk,
)
from committee.lots.derive import _fifo_relief, derive_lots
from committee.models import (
    Base,
    ImportBatch,
    Instrument,
    MarketObservation,
    PositionSnapshot,
    TaxLot,
    Transaction,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/lots_test.db")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _inst(session: Session, ticker: str, itype: str = "stock") -> Instrument:
    inst = Instrument(
        ticker=ticker,
        instrument_type=itype,
        sleeve="equity_us",
        is_cash_equivalent=False,
        needs_unwind=(itype == "stock"),
        aliases=[],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


def _batch(session: Session, ftype: str = "transactions") -> ImportBatch:
    batch = ImportBatch(
        file_hash=str(uuid.uuid4()),
        original_filename="test.csv",
        file_type=ftype,
        row_count=1,
    )
    session.add(batch)
    session.flush()
    return batch


def _buy(
    session: Session,
    inst: Instrument,
    acct: str,
    trade_date: date,
    qty: str,
    price: str,
    fees: str = "0",
    batch: ImportBatch | None = None,
) -> Transaction:
    b = batch or _batch(session)
    t = Transaction(
        batch_id=b.id,
        account_id=acct,
        instrument_id=inst.id,
        raw_instrument=inst.ticker,
        trade_date=trade_date,
        raw_type="BUY",
        canonical_type="buy",
        qty=Decimal(qty),
        price=Decimal(price),
        fees=Decimal(fees),
    )
    session.add(t)
    session.flush()
    return t


def _sell(
    session: Session,
    inst: Instrument,
    acct: str,
    trade_date: date,
    qty: str,
    batch: ImportBatch | None = None,
) -> Transaction:
    b = batch or _batch(session)
    t = Transaction(
        batch_id=b.id,
        account_id=acct,
        instrument_id=inst.id,
        raw_instrument=inst.ticker,
        trade_date=trade_date,
        raw_type="SELL",
        canonical_type="sell",
        qty=Decimal(qty),
        price=Decimal("100"),  # price not needed for relief
    )
    session.add(t)
    session.flush()
    return t


def _snap(
    session: Session,
    inst: Instrument,
    acct: str,
    qty: str,
    cost_basis: str | None,
    as_of: date = date(2024, 1, 1),
) -> PositionSnapshot:
    batch = _batch(session, "positions")
    snap = PositionSnapshot(
        batch_id=batch.id,
        account_id=acct,
        instrument_id=inst.id,
        raw_instrument=inst.ticker,
        as_of=as_of,
        qty=Decimal(qty),
        cost_basis=Decimal(cost_basis) if cost_basis else None,
    )
    session.add(snap)
    session.flush()
    return snap


def _price(session: Session, inst: Instrument, price: str, as_of: date = date(2024, 6, 1)) -> None:
    session.add(
        MarketObservation(
            instrument_id=inst.id,
            series_id=f"price_{inst.ticker}",
            observed_date=as_of,
            value=Decimal(price),
            unit="USD_adj_close",
            degraded=False,
            source="test",
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# _fifo_relief unit tests
# ---------------------------------------------------------------------------


def test_fifo_relief_basic():
    """buy 100@$10, buy 50@$20, sell 60 → 40@$10 + 50@$20."""
    buys = [
        (date(2022, 1, 1), Decimal("100"), Decimal("10")),
        (date(2022, 6, 1), Decimal("50"), Decimal("20")),
    ]
    sells = [(date(2023, 1, 1), Decimal("60"))]
    lots = _fifo_relief(buys, sells)
    assert len(lots) == 2
    assert lots[0] == (date(2022, 1, 1), Decimal("40"), Decimal("10"))
    assert lots[1] == (date(2022, 6, 1), Decimal("50"), Decimal("20"))


def test_fifo_relief_full_consume():
    """Sell exactly equals buy → no open lots."""
    buys = [(date(2022, 1, 1), Decimal("50"), Decimal("10"))]
    sells = [(date(2023, 1, 1), Decimal("50"))]
    lots = _fifo_relief(buys, sells)
    assert lots == []


def test_fifo_relief_no_sells():
    """No sells → buys returned unchanged."""
    buys = [
        (date(2022, 1, 1), Decimal("100"), Decimal("10")),
        (date(2022, 6, 1), Decimal("50"), Decimal("20")),
    ]
    lots = _fifo_relief(buys, [])
    assert len(lots) == 2
    total_qty = sum(q for _, q, _ in lots)
    assert total_qty == Decimal("150")


def test_fifo_relief_oversell_ignored():
    """Sell more than held → remaining consumed, no negative lots."""
    buys = [(date(2022, 1, 1), Decimal("10"), Decimal("10"))]
    sells = [(date(2023, 1, 1), Decimal("100"))]
    lots = _fifo_relief(buys, sells)
    assert lots == []


def test_fifo_lot_qty_matches_holding():
    """Sum of derived lot qtys matches holding after buy-sell sequence."""
    buys = [
        (date(2022, 1, 1), Decimal("100"), Decimal("10")),
        (date(2022, 6, 1), Decimal("50"), Decimal("20")),
    ]
    sells = [(date(2023, 1, 1), Decimal("60"))]
    lots = _fifo_relief(buys, sells)
    total = sum(q for _, q, _ in lots)
    assert total == Decimal("90")


# ---------------------------------------------------------------------------
# derive_lots DB tests
# ---------------------------------------------------------------------------


def test_derive_exact_lots(db_session):
    """buy 100@$10, buy 50@$20, sell 60 → two TaxLot rows with FIFO-relieved quantities."""
    inst = _inst(db_session, "AAPL")
    b = _batch(db_session)
    _buy(db_session, inst, "brokerage", date(2022, 1, 1), "100", "10.00", batch=b)
    _buy(db_session, inst, "brokerage", date(2022, 6, 1), "50", "20.00", batch=b)
    _sell(db_session, inst, "brokerage", date(2023, 1, 1), "60", batch=b)
    db_session.flush()

    count = derive_lots(db_session)
    assert count == 2

    lots = db_session.query(TaxLot).filter_by(instrument_id=inst.id).order_by(TaxLot.acquired_date).all()
    assert lots[0].qty == Decimal("40")
    assert lots[0].cost_per_share == Decimal("10.00")
    assert lots[0].basis_quality == "exact"
    assert lots[1].qty == Decimal("50")
    assert lots[1].cost_per_share == Decimal("20.00")

    total_qty = sum(lot.qty for lot in lots)
    assert total_qty == Decimal("90")


def test_derive_exact_lots_includes_fees(db_session):
    """cost_per_share = price + fees/qty."""
    inst = _inst(db_session, "MSFT")
    _buy(db_session, inst, "brokerage", date(2023, 1, 1), "100", "100.00", fees="5.00")
    db_session.flush()

    derive_lots(db_session)
    lot = db_session.query(TaxLot).filter_by(instrument_id=inst.id).one()
    assert lot.cost_per_share == Decimal("100.05")  # 100 + 5/100


def test_derive_average_fallback(db_session):
    """Position snapshot with cost_basis, no buy transactions → average_fallback lot."""
    inst = _inst(db_session, "NVDA")
    _snap(db_session, inst, "brokerage", "10", "150.00")
    db_session.flush()

    count = derive_lots(db_session)
    assert count == 1

    lot = db_session.query(TaxLot).filter_by(instrument_id=inst.id).one()
    assert lot.basis_quality == "average_fallback"
    assert lot.cost_per_share == Decimal("15.00")  # 150 / 10
    assert lot.qty == Decimal("10")


def test_no_lot_when_no_cost_basis(db_session):
    """Position snapshot without cost_basis → no lot created (Invariant H)."""
    inst = _inst(db_session, "TSLA")
    _snap(db_session, inst, "brokerage", "100", cost_basis=None)
    db_session.flush()

    count = derive_lots(db_session)
    assert count == 0
    assert db_session.query(TaxLot).count() == 0


def test_derive_lots_idempotent(db_session):
    """Running derive_lots twice produces the same result."""
    inst = _inst(db_session, "GOOG")
    _buy(db_session, inst, "brokerage", date(2022, 1, 1), "10", "100.00")
    db_session.flush()

    count1 = derive_lots(db_session)
    db_session.flush()
    count2 = derive_lots(db_session)
    db_session.flush()

    assert count1 == count2
    assert db_session.query(TaxLot).count() == count2


def test_exact_lots_skip_fallback_for_same_account(db_session):
    """If buy transactions exist for an account, no fallback lot for the same account."""
    inst = _inst(db_session, "META")
    _buy(db_session, inst, "brokerage", date(2022, 1, 1), "50", "200.00")
    # Snapshot with cost_basis — should be ignored for this account
    _snap(db_session, inst, "brokerage", "50", "10000.00")
    db_session.flush()

    derive_lots(db_session)
    lots = db_session.query(TaxLot).filter_by(instrument_id=inst.id).all()
    assert all(lot.basis_quality == "exact" for lot in lots)
    assert len(lots) == 1


def test_fallback_and_exact_for_different_accounts(db_session):
    """Buy transactions in account A → exact lots; snapshot-only in account B → fallback."""
    inst = _inst(db_session, "AMZN")
    _buy(db_session, inst, "ira", date(2022, 1, 1), "20", "150.00")
    _snap(db_session, inst, "brokerage", "15", "2250.00")
    db_session.flush()

    derive_lots(db_session)
    lots = db_session.query(TaxLot).filter_by(instrument_id=inst.id).order_by(TaxLot.account_id).all()
    assert len(lots) == 2
    acct_quality = {lot.account_id: lot.basis_quality for lot in lots}
    assert acct_quality["ira"] == "exact"
    assert acct_quality["brokerage"] == "average_fallback"


# ---------------------------------------------------------------------------
# ST/LT classification
# ---------------------------------------------------------------------------


def test_lt_boundary_exact_one_year_is_short_term():
    """Acquired 2024-01-01, as_of 2025-01-01 → exactly 1 year → still short-term."""
    assert is_long_term(date(2024, 1, 1), date(2025, 1, 1)) is False


def test_lt_boundary_one_day_past_one_year_is_long_term():
    """Acquired 2024-01-01, as_of 2025-01-02 → more than 1 year → long-term."""
    assert is_long_term(date(2024, 1, 1), date(2025, 1, 2)) is True


def test_lt_boundary_leap_year():
    """Acquired 2024-02-28, as_of 2025-02-28 → exactly 1 year → short-term."""
    assert is_long_term(date(2024, 2, 28), date(2025, 2, 28)) is False
    assert is_long_term(date(2024, 2, 28), date(2025, 3, 1)) is True


def test_lt_already_long_term():
    """Already LT → days_to_long_term returns None."""
    assert days_to_long_term(date(2020, 1, 1), date(2024, 1, 1)) is None


def test_days_to_lt_positive_when_short_term():
    """ST lot → positive days_to_lt."""
    acquired = date(2024, 1, 1)
    as_of = date(2024, 6, 1)
    dtlt = days_to_long_term(acquired, as_of)
    assert dtlt is not None
    assert dtlt > 0
    # Should be: (2024-01-01 + 1 year + 1 day) - 2024-06-01 = 2025-01-02 - 2024-06-01
    assert dtlt == (date(2025, 1, 2) - date(2024, 6, 1)).days


# ---------------------------------------------------------------------------
# Gain/loss and estimated tax
# ---------------------------------------------------------------------------


def test_lot_gain_profit():
    lot = TaxLot(qty=Decimal("100"), cost_per_share=Decimal("10"))
    assert lot_gain(lot, Decimal("15")) == Decimal("500")


def test_lot_gain_loss():
    lot = TaxLot(qty=Decimal("100"), cost_per_share=Decimal("20"))
    assert lot_gain(lot, Decimal("15")) == Decimal("-500")


def test_estimated_tax_lt():
    assert estimated_tax(Decimal("1000"), True, Decimal("0.37"), Decimal("0.20")) == Decimal("200")


def test_estimated_tax_st():
    assert estimated_tax(Decimal("1000"), False, Decimal("0.37"), Decimal("0.20")) == Decimal("370")


def test_estimated_tax_loss_benefit():
    """Loss generates negative tax (benefit)."""
    assert estimated_tax(Decimal("-1000"), False, Decimal("0.37"), Decimal("0.20")) == Decimal("-370")


# ---------------------------------------------------------------------------
# Wash-sale detection
# ---------------------------------------------------------------------------


def test_wash_sale_risk_recent_buy(db_session):
    """Buy 20 days before proposed sell date → wash-sale risk True."""
    inst = _inst(db_session, "PYPL")
    today = date(2024, 6, 1)
    _buy(db_session, inst, "brokerage", date(2024, 5, 12), "10", "50.00")
    db_session.flush()

    assert wash_sale_risk(inst.id, "brokerage", db_session, today) is True


def test_wash_sale_risk_old_buy(db_session):
    """Buy 60 days before proposed sell → outside window → no wash-sale risk."""
    inst = _inst(db_session, "CRM")
    today = date(2024, 6, 1)
    _buy(db_session, inst, "brokerage", date(2024, 4, 1), "10", "50.00")
    db_session.flush()

    assert wash_sale_risk(inst.id, "brokerage", db_session, today) is False


def test_wash_sale_risk_no_buys(db_session):
    """No buys at all → no wash-sale risk."""
    inst = _inst(db_session, "INTC")
    db_session.flush()

    assert wash_sale_risk(inst.id, "brokerage", db_session, date(2024, 6, 1)) is False


# ---------------------------------------------------------------------------
# compute_unwind_analysis ordering
# ---------------------------------------------------------------------------


def test_unwind_ordering(db_session):
    """Losses first, LT gains second, ST gains last."""
    inst = _inst(db_session, "XOM")  # needs_unwind=True (stock)
    acct = "brokerage"
    today = date(2024, 6, 1)

    # Lot 1: old (LT), gain
    session = db_session
    b = _batch(session)
    _buy(session, inst, acct, date(2020, 1, 1), "10", "50.00", batch=b)   # LT gain at $100
    # Lot 2: recent (ST), gain
    _buy(session, inst, acct, date(2024, 1, 1), "10", "80.00", batch=b)   # ST gain at $100
    # Lot 3: recent (ST), loss
    _buy(session, inst, acct, date(2024, 3, 1), "10", "120.00", batch=b)  # ST loss at $100
    session.flush()

    _price(session, inst, "100.00", today)
    session.flush()

    derive_lots(session)
    session.flush()

    analyses = compute_unwind_analysis(session, today, Decimal("0.37"), Decimal("0.20"))
    assert len(analyses) == 3

    gains = [a.unrealized_gain for a in analyses]
    # First = loss (negative)
    assert gains[0] < Decimal("0")
    # Second = LT gain (long_term=True)
    assert analyses[1].long_term is True
    assert gains[1] >= Decimal("0")
    # Third = ST gain
    assert analyses[2].long_term is False
    assert gains[2] >= Decimal("0")


def test_unwind_no_price_shows_none(db_session):
    """When no price data exists, unrealized_gain is None."""
    inst = _inst(db_session, "GME")
    _buy(db_session, inst, "brokerage", date(2022, 1, 1), "10", "200.00")
    db_session.flush()

    derive_lots(db_session)
    db_session.flush()

    today = date(2024, 6, 1)
    analyses = compute_unwind_analysis(db_session, today, Decimal("0.37"), Decimal("0.20"))
    assert len(analyses) == 1
    assert analyses[0].unrealized_gain is None
    assert analyses[0].current_price is None


def test_unwind_funds_excluded(db_session):
    """ETF instruments (needs_unwind=False) are excluded from unwind analysis."""
    stock = _inst(db_session, "AAPL", itype="stock")
    fund = _inst(db_session, "VTI", itype="etf")
    _buy(db_session, stock, "brokerage", date(2022, 1, 1), "10", "100.00")
    _buy(db_session, fund, "brokerage", date(2022, 1, 1), "10", "100.00")
    db_session.flush()

    derive_lots(db_session)
    _price(db_session, stock, "150.00")
    _price(db_session, fund, "150.00")
    db_session.flush()

    today = date(2024, 6, 1)
    analyses = compute_unwind_analysis(db_session, today, Decimal("0.37"), Decimal("0.20"))
    tickers = {a.instrument.ticker for a in analyses}
    assert "AAPL" in tickers
    assert "VTI" not in tickers


# ── WP-5: Lot correction tests ──────────────────────────────────────────

from committee.lots.corrections import (  # noqa: E402
    CorrectionConflict,
    add_correction,
    get_active_corrections,
    supersede_correction,
    validate_correction,
)
from committee.models import LotCorrection  # noqa: E402


def test_add_correction_creates_active_row(db_session):
    """add_correction creates an active LotCorrection row."""
    inst = _inst(db_session, "CORR1")
    corr = add_correction(
        session=db_session,
        account_key="ACCT-TEST",
        instrument_id=inst.id,
        acquired_date=date(2022, 3, 15),
        qty=Decimal("50.0000"),
        cost_per_share=Decimal("142.5000"),
        basis_quality="user_asserted",
        effective_date=date(2024, 1, 1),
        reason="Test correction",
    )
    db_session.flush()
    assert corr.id is not None
    assert corr.validation_status == "active"
    assert corr.supersedes_id is None


def test_supersede_creates_new_marks_old_superseded(db_session):
    """supersede_correction creates a new row and marks prior as superseded."""
    inst = _inst(db_session, "CORR2")
    c1 = add_correction(
        db_session, "ACCT-TEST", inst.id,
        date(2022, 3, 15), Decimal("50.0000"), Decimal("142.5000"),
        "user_asserted", date(2024, 1, 1), "first",
    )
    db_session.flush()
    c2 = supersede_correction(
        db_session, c1.id, Decimal("48.0000"), Decimal("143.0000"), "qty correction",
    )
    db_session.flush()
    assert c2.supersedes_id == c1.id
    assert c2.validation_status == "active"
    db_session.refresh(c1)
    assert c1.validation_status == "superseded"


def test_get_active_corrections_filters_superseded(db_session):
    """get_active_corrections excludes superseded rows."""
    inst = _inst(db_session, "CORR3")
    c1 = add_correction(
        db_session, "ACCT-TEST", inst.id,
        date(2022, 3, 15), Decimal("50.0000"), Decimal("142.5000"),
        "user_asserted", date(2024, 1, 1), "first",
    )
    db_session.flush()
    supersede_correction(db_session, c1.id, Decimal("48.0000"), Decimal("143.0000"), "fix")
    db_session.flush()
    active = get_active_corrections(db_session, instrument_id=inst.id)
    ids = [c.id for c in active]
    assert c1.id not in ids  # superseded


def test_correction_does_not_change_holdings_qty(db_session):
    """Applying a correction must not change holding quantities (AC-8)."""
    from sqlalchemy import select as sa_select

    from committee.models import Holding

    inst = _inst(db_session, "CORR4")

    qty_before = {
        h.instrument_id: h.qty
        for h in db_session.execute(sa_select(Holding)).scalars().all()
        if h.qty is not None
    }
    add_correction(
        db_session, "ACCT-TEST", inst.id,
        date(2022, 3, 15), Decimal("5.0000"), Decimal("100.0000"),
        "user_asserted", date(2024, 1, 1), "test",
    )
    db_session.flush()
    qty_after = {
        h.instrument_id: h.qty
        for h in db_session.execute(sa_select(Holding)).scalars().all()
        if h.qty is not None
    }
    assert qty_before == qty_after
