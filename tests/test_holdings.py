"""Tests for holdings derivation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from committee.holdings import HoldingRow, household_view, rebuild_holdings, resolve_local
from committee.models import Holding, ImportBatch, Instrument, PositionSnapshot

# ── Helpers ───────────────────────────────────────────────────────────────────

def _batch(session: Session, filename: str = "test.csv", file_type: str = "positions") -> ImportBatch:
    b = ImportBatch(
        file_hash=f"hash_{filename}_{datetime.now().timestamp()}",
        original_filename=filename,
        file_type=file_type,
        broker_fingerprint="fp_test",
        row_count=0,
        imported_at=datetime.now(),
    )
    session.add(b)
    session.flush()
    return b


def _snap(
    session: Session,
    batch: ImportBatch,
    raw: str,
    qty: str = "100",
    mkt_val: str = "10000",
    account: str | None = "acct1",
    as_of: date | None = None,
) -> PositionSnapshot:
    s = PositionSnapshot(
        batch_id=batch.id,
        account_id=account,
        raw_instrument=raw,
        as_of=as_of or date(2026, 6, 1),
        qty=Decimal(qty),
        market_value=Decimal(mkt_val),
    )
    session.add(s)
    session.flush()
    return s


def _inst(
    session: Session,
    ticker: str,
    name: str = "Test Corp",
    itype: str = "stock",
    aliases: list[str] | None = None,
) -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=name,
        instrument_type=itype,
        asset_class="equity",
        sleeve="unclassified",
        is_cash_equivalent=(itype == "cash"),
        needs_unwind=(itype == "stock"),
        aliases=aliases or [],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


# ── resolve_local ─────────────────────────────────────────────────────────────

def test_resolve_local_exact(db_session: Session) -> None:
    _inst(db_session, "AAPL", "Apple Inc")
    inst = resolve_local("AAPL", db_session)
    assert inst is not None
    assert inst.ticker == "AAPL"


def test_resolve_local_normalized(db_session: Session) -> None:
    _inst(db_session, "BRK/B", "Berkshire B")
    inst = resolve_local("BRK.B", db_session)
    assert inst is not None
    assert inst.ticker == "BRK/B"


def test_resolve_local_via_alias_field(db_session: Session) -> None:
    _inst(db_session, "AAPL", "Apple Inc", aliases=["Apple Inc Alias"])
    inst = resolve_local("Apple Inc Alias", db_session)
    assert inst is not None
    assert inst.ticker == "AAPL"


def test_resolve_local_sweep(db_session: Session) -> None:
    """SPAXX is in SWEEP_ALIASES → should resolve to $CASH if that instrument exists."""
    _inst(db_session, "$CASH", "Cash", itype="cash")
    inst = resolve_local("SPAXX", db_session)
    assert inst is not None
    assert inst.ticker == "$CASH"


def test_resolve_local_unknown_returns_none(db_session: Session) -> None:
    inst = resolve_local("ZZZUNKNOWN", db_session)
    assert inst is None


# ── rebuild_holdings ──────────────────────────────────────────────────────────

def test_rebuild_holdings_basic(db_session: Session) -> None:
    _inst(db_session, "AAPL", "Apple Inc")
    b = _batch(db_session)
    _snap(db_session, b, "AAPL", qty="50", mkt_val="9000")
    rows = rebuild_holdings(db_session)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].qty == Decimal("50")
    assert rows[0].is_resolved is True


def test_rebuild_holdings_unresolved(db_session: Session) -> None:
    b = _batch(db_session)
    _snap(db_session, b, "UNKNOWN_TICKER")
    rows = rebuild_holdings(db_session)
    assert len(rows) == 1
    assert rows[0].is_resolved is False
    assert rows[0].ticker is None
    assert rows[0].raw_instrument == "UNKNOWN_TICKER"


def test_rebuild_holdings_latest_snapshot_wins(db_session: Session) -> None:
    """When two batches have the same instrument, only the latest snapshot is used."""
    _inst(db_session, "AAPL", "Apple Inc")
    b1 = _batch(db_session, "file1.csv")
    b1.imported_at = datetime(2026, 5, 1)
    _snap(db_session, b1, "AAPL", qty="100")

    b2 = _batch(db_session, "file2.csv")
    b2.imported_at = datetime(2026, 6, 1)
    _snap(db_session, b2, "AAPL", qty="150")
    db_session.flush()

    rows = rebuild_holdings(db_session)
    aapl_rows = [r for r in rows if r.ticker == "AAPL"]
    assert len(aapl_rows) == 1
    assert aapl_rows[0].qty == Decimal("150")


def test_rebuild_persists_resolved_to_holdings_table(db_session: Session) -> None:
    _inst(db_session, "VTI", "Vanguard ETF", itype="etf")
    b = _batch(db_session)
    _snap(db_session, b, "VTI", qty="200", mkt_val="48000")
    rebuild_holdings(db_session)
    persisted = db_session.query(Holding).all()
    assert len(persisted) == 1
    assert persisted[0].qty == Decimal("200")


def test_rebuild_does_not_persist_unresolved(db_session: Session) -> None:
    b = _batch(db_session)
    _snap(db_session, b, "ZZZUNKNOWN")
    rebuild_holdings(db_session)
    persisted = db_session.query(Holding).all()
    assert len(persisted) == 0


def test_rebuild_truncates_previous_holdings(db_session: Session) -> None:
    """Second rebuild replaces the first — holdings table never accumulates stale rows."""
    _inst(db_session, "AAPL", "Apple Inc")
    b = _batch(db_session)
    _snap(db_session, b, "AAPL", qty="100")
    rebuild_holdings(db_session)
    rebuild_holdings(db_session)  # second rebuild
    persisted = db_session.query(Holding).all()
    assert len(persisted) == 1  # not 2


def test_rebuild_per_account(db_session: Session) -> None:
    _inst(db_session, "AAPL", "Apple Inc")
    b = _batch(db_session)
    _snap(db_session, b, "AAPL", qty="50", account="acct1")
    _snap(db_session, b, "AAPL", qty="75", account="acct2")
    rows = rebuild_holdings(db_session)
    assert len(rows) == 2
    accts = {r.account_id for r in rows}
    assert accts == {"acct1", "acct2"}


# ── household_view ────────────────────────────────────────────────────────────

def test_household_aggregates_qty(db_session: Session) -> None:
    rows = [
        HoldingRow("AAPL", 1, "AAPL", "Apple", "stock", "acct1", date(2026, 6, 1),
                   Decimal("50"), Decimal("9000"), True),
        HoldingRow("AAPL", 1, "AAPL", "Apple", "stock", "acct2", date(2026, 6, 1),
                   Decimal("75"), Decimal("13500"), True),
    ]
    hh = household_view(rows)
    assert len(hh) == 1
    assert hh[0].qty == Decimal("125")
    assert hh[0].market_value == Decimal("22500")
    assert hh[0].account_id is None


def test_household_multiple_instruments(db_session: Session) -> None:
    rows = [
        HoldingRow("AAPL", 1, "AAPL", "Apple", "stock", "acct1", date(2026, 6, 1),
                   Decimal("50"), Decimal("9000"), True),
        HoldingRow("VTI", 2, "VTI", "Vanguard", "etf", "acct1", date(2026, 6, 1),
                   Decimal("200"), Decimal("48000"), True),
    ]
    hh = household_view(rows)
    assert len(hh) == 2


def test_holdings_invariant_b_snapshot_untouched(db_session: Session) -> None:
    """Invariant B: rebuild_holdings must never modify PositionSnapshot rows."""
    _inst(db_session, "AAPL", "Apple Inc")
    b = _batch(db_session)
    snap = _snap(db_session, b, "AAPL", qty="100")
    original_qty = snap.qty

    rebuild_holdings(db_session)

    db_session.refresh(snap)
    assert snap.qty == original_qty
    assert snap.instrument_id is None  # never updated
