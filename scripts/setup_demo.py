"""Populate demo.db with resolved instruments and linked snapshots for dashboard demo."""
import sys
sys.path.insert(0, "src")

from decimal import Decimal
from pathlib import Path
from datetime import date

from committee.db import init_db, get_session
from committee.models import Instrument, Holding, Account, PositionSnapshot, MarketObservation
from sqlalchemy import select

DB = Path("data/demo.db")
init_db(DB)

INSTRUMENTS = [
    dict(ticker="VTSAX", name="Vanguard Total Stock Market Index Fund Admiral Shares",
         instrument_type="mutual_fund", sleeve="equity_us", is_cash_equivalent=False),
    dict(ticker="VTIAX", name="Vanguard Total International Stock Index Fund Admiral Shares",
         instrument_type="mutual_fund", sleeve="equity_intl", is_cash_equivalent=False),
    dict(ticker="VBTLX", name="Vanguard Total Bond Market Index Fund Admiral Shares",
         instrument_type="mutual_fund", sleeve="fixed_income", is_cash_equivalent=False),
    dict(ticker="VMFXX", name="Vanguard Federal Money Market Fund",
         instrument_type="mutual_fund", sleeve="cash", is_cash_equivalent=True),
    dict(ticker="VFIFX", name="Vanguard Target Retirement 2050 Fund",
         instrument_type="mutual_fund", sleeve="alternatives", is_cash_equivalent=False),
]

MARKET_DATA = [
    dict(source="fred", series_id="T10Y3M", observed_date=date(2024, 12, 31), value=Decimal("0.25"), degraded=False),
    dict(source="fred", series_id="BAMLH0A0HYM2", observed_date=date(2024, 12, 31), value=Decimal("3.10"), degraded=False),
    dict(source="fred", series_id="BAMLH0A0HYM2", observed_date=date(2024, 11, 30), value=Decimal("3.05"), degraded=False),
    dict(source="fred", series_id="VIXCLS", observed_date=date(2024, 12, 31), value=Decimal("17.5"), degraded=False),
    dict(source="fred", series_id="UNRATE", observed_date=date(2024, 12, 31), value=Decimal("4.1"), degraded=False),
]

for session in get_session():
    ticker_to_id: dict[str, int] = {}
    for d in INSTRUMENTS:
        existing = session.execute(
            select(Instrument).where(Instrument.ticker == d["ticker"])
        ).scalar_one_or_none()
        if existing:
            ticker_to_id[d["ticker"]] = existing.id
            print(f"  instrument exists: {d['ticker']} id={existing.id}")
        else:
            inst = Instrument(
                ticker=d["ticker"],
                name=d["name"],
                instrument_type=d["instrument_type"],
                sleeve=d["sleeve"],
                is_cash_equivalent=d["is_cash_equivalent"],
                needs_unwind=False,
                aliases=[],
                bundle_tags=[],
            )
            session.add(inst)
            session.flush()
            ticker_to_id[d["ticker"]] = inst.id
            print(f"  created instrument: {d['ticker']} id={inst.id}")

    # Create a demo account
    acct = session.execute(
        select(Account).where(Account.account_key == "DEMO_VANGUARD")
    ).scalar_one_or_none()
    if not acct:
        acct = Account(account_key="DEMO_VANGUARD", broker="vanguard", tax_type="taxable", label="Vanguard Brokerage")
        session.add(acct)
        session.flush()
        print(f"  created account: DEMO_VANGUARD id={acct.id}")
    else:
        print(f"  account exists: DEMO_VANGUARD id={acct.id}")

    # Link position_snapshots to instruments
    snapshots = session.execute(
        select(PositionSnapshot).where(PositionSnapshot.instrument_id.is_(None))
    ).scalars().all()
    for snap in snapshots:
        raw = snap.raw_instrument
        if raw and raw in ticker_to_id:
            snap.instrument_id = ticker_to_id[raw]
            snap.account_id = acct.id
            print(f"  linked snapshot {snap.id}: {raw} -> instrument {ticker_to_id[raw]}")

    # Create holdings from position snapshots
    for ticker, inst_id in ticker_to_id.items():
        snap = session.execute(
            select(PositionSnapshot).where(PositionSnapshot.instrument_id == inst_id)
        ).scalar_one_or_none()
        if not snap:
            continue
        existing = session.execute(
            select(Holding).where(Holding.instrument_id == inst_id)
        ).scalar_one_or_none()
        if not existing:
            h = Holding(
                instrument_id=inst_id,
                account_id=str(acct.id),
                qty=Decimal(str(snap.qty)),
                market_value=Decimal(str(snap.market_value)) if snap.market_value else None,
                as_of=date(2024, 12, 31),
            )
            session.add(h)
            print(f"  created holding: {ticker} qty={snap.qty}")
        else:
            print(f"  holding exists: {ticker}")

    # Add macro market observations
    for obs_data in MARKET_DATA:
        existing = session.execute(
            select(MarketObservation).where(
                MarketObservation.series_id == obs_data["series_id"],
                MarketObservation.observed_date == obs_data["observed_date"],
            )
        ).scalar_one_or_none()
        if not existing:
            obs = MarketObservation(
                source=obs_data["source"],
                series_id=obs_data["series_id"],
                observed_date=obs_data["observed_date"],
                value=obs_data["value"],
                degraded=obs_data["degraded"],
            )
            session.add(obs)
            print(f"  added market obs: {obs_data['series_id']} {obs_data['observed_date']}")

    print("\nDone.")
