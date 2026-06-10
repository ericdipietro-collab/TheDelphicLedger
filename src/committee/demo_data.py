"""Synthetic demo database seeder.

Generates a realistic ~$285K household portfolio with stocks, ETFs, price
history, fundamentals, and macro observations — enough for all six oracles
to score and produce meaningful proposals.

All data is synthetic and publicly calibrated from approximate market values.
None of this reflects a real person's holdings.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import (
    Account,
    Fundamental,
    Holding,
    ImportBatch,
    Instrument,
    MarketObservation,
    PositionSnapshot,
)

# ── Portfolio definition ───────────────────────────────────────────────────────

_ACCOUNT = dict(
    account_key="DEMO_BROKERAGE",
    broker="fidelity",
    tax_type="taxable",
    label="Fidelity Brokerage (Demo)",
)

# (ticker, name, type, sleeve, is_cash, qty, price)
_HOLDINGS: list[tuple[str, str, str, str, bool, str, str]] = [
    # US Equity — stocks
    ("AAPL",  "Apple Inc.",                              "stock",       "equity_us",    False, "100",   "185.00"),
    ("MSFT",  "Microsoft Corporation",                   "stock",       "equity_us",    False, "55",    "375.00"),
    ("JNJ",   "Johnson & Johnson",                       "stock",       "equity_us",    False, "110",   "155.00"),
    ("KO",    "The Coca-Cola Company",                   "stock",       "equity_us",    False, "250",   "62.00"),
    ("V",     "Visa Inc.",                               "stock",       "equity_us",    False, "42",    "275.00"),
    # US Equity — ETFs
    ("SPY",   "SPDR S&P 500 ETF Trust",                 "etf",         "equity_us",    False, "38",    "500.00"),
    ("SCHD",  "Schwab US Dividend Equity ETF",           "etf",         "equity_us",    False, "300",   "79.00"),
    # Intl Equity
    ("VXUS",  "Vanguard Total Intl Stock ETF",           "etf",         "equity_intl",  False, "480",   "62.00"),
    # Fixed Income
    ("AGG",   "iShares Core US Aggregate Bond ETF",      "etf",         "fixed_income", False, "530",   "100.00"),
    ("BND",   "Vanguard Total Bond Market ETF",          "etf",         "fixed_income", False, "380",   "73.00"),
    # Alternatives
    ("VNQ",   "Vanguard Real Estate ETF",                "etf",         "alternatives", False, "240",   "90.00"),
    # Cash
    ("SPAXX", "Fidelity Government Money Market Fund",   "money_market","cash",         True,  "14500", "1.00"),
]

# ── Fundamental data (per stock) ───────────────────────────────────────────────
# (metric, period_end, value)
# Approximate public data, clearly synthetic.
_FUNDAMENTALS: dict[str, list[tuple[str, str, str]]] = {
    "AAPL": [
        ("revenue",           "2024-09-30", "391035000000"),
        ("revenue",           "2023-09-30", "383285000000"),
        ("gross_profit",      "2024-09-30", "173858000000"),
        ("gross_profit",      "2023-09-30", "169148000000"),
        ("net_income",        "2024-09-30", "93736000000"),
        ("operating_income",  "2024-09-30", "114301000000"),
        ("total_assets",      "2024-09-30", "364980000000"),
        ("equity",            "2024-09-30", "56950000000"),
        ("total_debt",        "2024-09-30", "104590000000"),
        ("cfo",               "2024-09-30", "118254000000"),
        ("capex",             "2024-09-30", "9447000000"),
        ("eps_diluted",       "2024-09-30", "6.11"),
        ("shares_outstanding","2024-09-30", "15343783000"),
    ],
    "MSFT": [
        ("revenue",           "2024-06-30", "245122000000"),
        ("revenue",           "2023-06-30", "211915000000"),
        ("gross_profit",      "2024-06-30", "171008000000"),
        ("gross_profit",      "2023-06-30", "146052000000"),
        ("net_income",        "2024-06-30", "88136000000"),
        ("operating_income",  "2024-06-30", "109433000000"),
        ("total_assets",      "2024-06-30", "512163000000"),
        ("equity",            "2024-06-30", "268477000000"),
        ("total_debt",        "2024-06-30", "79370000000"),
        ("cfo",               "2024-06-30", "132351000000"),
        ("capex",             "2024-06-30", "44482000000"),
        ("eps_diluted",       "2024-06-30", "11.80"),
        ("shares_outstanding","2024-06-30", "7432000000"),
    ],
    "JNJ": [
        ("revenue",           "2023-12-31", "85159000000"),
        ("revenue",           "2022-12-31", "93775000000"),
        ("gross_profit",      "2023-12-31", "57122000000"),
        ("gross_profit",      "2022-12-31", "56140000000"),
        ("net_income",        "2023-12-31", "35153000000"),
        ("operating_income",  "2023-12-31", "15649000000"),
        ("total_assets",      "2023-12-31", "182018000000"),
        ("equity",            "2023-12-31", "71476000000"),
        ("total_debt",        "2023-12-31", "30021000000"),
        ("cfo",               "2023-12-31", "18713000000"),
        ("capex",             "2023-12-31", "4119000000"),
        ("eps_diluted",       "2023-12-31", "9.00"),
        ("shares_outstanding","2023-12-31", "2405000000"),
    ],
    "KO": [
        ("revenue",           "2023-12-31", "45754000000"),
        ("revenue",           "2022-12-31", "43004000000"),
        ("gross_profit",      "2023-12-31", "27326000000"),
        ("gross_profit",      "2022-12-31", "25004000000"),
        ("net_income",        "2023-12-31", "10714000000"),
        ("operating_income",  "2023-12-31", "11311000000"),
        ("total_assets",      "2023-12-31", "94754000000"),
        ("equity",            "2023-12-31", "25941000000"),
        ("total_debt",        "2023-12-31", "35545000000"),
        ("cfo",               "2023-12-31", "11599000000"),
        ("capex",             "2023-12-31", "2054000000"),
        ("eps_diluted",       "2023-12-31", "2.47"),
        ("shares_outstanding","2023-12-31", "4299000000"),
    ],
    "V": [
        ("revenue",           "2024-09-30", "35926000000"),
        ("revenue",           "2023-09-30", "32654000000"),
        ("gross_profit",      "2024-09-30", "28950000000"),
        ("gross_profit",      "2023-09-30", "26250000000"),
        ("net_income",        "2024-09-30", "19742000000"),
        ("operating_income",  "2024-09-30", "22190000000"),
        ("total_assets",      "2024-09-30", "91969000000"),
        ("equity",            "2024-09-30", "38074000000"),
        ("total_debt",        "2024-09-30", "20610000000"),
        ("cfo",               "2024-09-30", "22090000000"),
        ("capex",             "2024-09-30", "764000000"),
        ("eps_diluted",       "2024-09-30", "10.06"),
        ("shares_outstanding","2024-09-30", "1968000000"),
    ],
}

# ETF expense ratios (fundamental metric)
_ETF_EXPENSE_RATIOS: dict[str, str] = {
    "SPY":  "0.0009",
    "SCHD": "0.0006",
    "VXUS": "0.0008",
    "AGG":  "0.0003",
    "BND":  "0.0003",
    "VNQ":  "0.0012",
}

# ETF distribution yields (MarketObservation unit="distribution_yield")
_ETF_DIST_YIELDS: dict[str, str] = {
    "SCHD": "0.0370",
    "VXUS": "0.0310",
    "AGG":  "0.0390",
    "BND":  "0.0415",
    "VNQ":  "0.0390",
    "SPY":  "0.0125",
}

# Stock quarterly dividends per share (paid quarterly)
_STOCK_DIVIDENDS: dict[str, str] = {
    "AAPL": "0.25",
    "MSFT": "0.75",
    "JNJ":  "1.19",
    "KO":   "0.485",
    "V":    "0.52",
}

# FRED macro series: list of (date_str, value_str) — roughly monthly
_MACRO: dict[str, list[tuple[str, str]]] = {
    "T10Y3M": [
        ("2024-01-31", "-1.42"), ("2024-02-28", "-1.25"), ("2024-03-31", "-1.10"),
        ("2024-04-30", "-0.95"), ("2024-05-31", "-0.80"), ("2024-06-30", "-0.62"),
        ("2024-07-31", "-0.48"), ("2024-08-31", "-0.33"), ("2024-09-30", "-0.18"),
        ("2024-10-31", "-0.08"), ("2024-11-30", "0.05"),  ("2024-12-31", "0.15"),
        ("2025-01-31", "0.22"),  ("2025-02-28", "0.28"),  ("2025-03-31", "0.32"),
    ],
    "BAMLH0A0HYM2": [
        ("2024-01-31", "3.25"), ("2024-02-28", "3.10"), ("2024-03-31", "3.05"),
        ("2024-04-30", "3.20"), ("2024-05-31", "3.10"), ("2024-06-30", "3.00"),
        ("2024-07-31", "3.15"), ("2024-08-31", "3.05"), ("2024-09-30", "2.95"),
        ("2024-10-31", "3.10"), ("2024-11-30", "3.00"), ("2024-12-31", "3.10"),
        ("2025-01-31", "3.05"), ("2025-02-28", "3.15"), ("2025-03-31", "3.20"),
    ],
    "VIXCLS": [
        ("2024-01-31", "14.8"), ("2024-02-28", "13.9"), ("2024-03-31", "13.0"),
        ("2024-04-30", "15.2"), ("2024-05-31", "12.9"), ("2024-06-30", "12.4"),
        ("2024-07-31", "16.4"), ("2024-08-31", "15.1"), ("2024-09-30", "16.7"),
        ("2024-10-31", "21.3"), ("2024-11-30", "14.6"), ("2024-12-31", "17.5"),
        ("2025-01-31", "16.2"), ("2025-02-28", "18.1"), ("2025-03-31", "20.4"),
    ],
    "CPILFESL": [
        ("2024-01-31", "316.0"), ("2024-02-28", "316.8"), ("2024-03-31", "317.5"),
        ("2024-04-30", "318.2"), ("2024-05-31", "318.9"), ("2024-06-30", "319.5"),
        ("2024-07-31", "320.0"), ("2024-08-31", "320.6"), ("2024-09-30", "321.2"),
        ("2024-10-31", "321.8"), ("2024-11-30", "322.4"), ("2024-12-31", "323.1"),
    ],
    "DTWEXBGS": [
        ("2024-01-31", "123.5"), ("2024-02-28", "124.1"), ("2024-03-31", "124.8"),
        ("2024-04-30", "125.5"), ("2024-05-31", "124.9"), ("2024-06-30", "123.8"),
        ("2024-07-31", "122.4"), ("2024-08-31", "121.0"), ("2024-09-30", "120.5"),
        ("2024-10-31", "121.8"), ("2024-11-30", "122.4"), ("2024-12-31", "121.5"),
    ],
    "UNRATE": [
        ("2024-01-31", "3.7"), ("2024-02-28", "3.9"), ("2024-03-31", "3.8"),
        ("2024-04-30", "3.9"), ("2024-05-31", "4.0"), ("2024-06-30", "4.1"),
        ("2024-07-31", "4.3"), ("2024-08-31", "4.2"), ("2024-09-30", "4.1"),
        ("2024-10-31", "4.1"), ("2024-11-30", "4.2"), ("2024-12-31", "4.2"),
    ],
}

# ── Price trajectory parameters ────────────────────────────────────────────────
# (start_price, annual_drift, annual_vol) — calibrated to generate realistic history
_PRICE_PARAMS: dict[str, tuple[float, float, float]] = {
    "AAPL":  (158.0,  0.18, 0.22),
    "MSFT":  (320.0,  0.22, 0.20),
    "JNJ":   (162.0, -0.04, 0.14),
    "KO":    (59.5,   0.04, 0.11),
    "V":     (240.0,  0.18, 0.18),
    "SPY":   (440.0,  0.22, 0.15),
    "SCHD":  (74.0,   0.08, 0.13),
    "VXUS":  (56.0,   0.10, 0.16),
    "AGG":   (96.0,  -0.02, 0.06),
    "BND":   (72.0,  -0.01, 0.06),
    "VNQ":   (82.0,   0.08, 0.18),
    "SPAXX": (1.0,    0.0,  0.0),
}


def _gen_prices(start: float, drift: float, vol: float, n_days: int) -> list[float]:
    """Deterministic GBM price path using an isolated Random instance."""
    rng = random.Random(int(start * 1000))  # isolated — no global state pollution
    dt = 1.0 / 252.0
    prices = [start]
    for _ in range(n_days - 1):
        z = rng.gauss(0.0, 1.0)
        ret = (drift - 0.5 * vol * vol) * dt + vol * (dt ** 0.5) * z
        prices.append(max(prices[-1] * (1.0 + ret), 0.01))
    return prices


def seed_demo_db(session: Session) -> None:
    """Populate session with a complete synthetic demo portfolio.

    Idempotent: safe to call multiple times on the same database.
    """
    today = date.today()

    # ── ImportBatch ────────────────────────────────────────────────────────────
    batch = session.execute(
        select(ImportBatch).where(ImportBatch.file_hash == "demo-synthetic-v2")
    ).scalar_one_or_none()
    if not batch:
        batch = ImportBatch(
            file_hash="demo-synthetic-v2",
            original_filename="demo_portfolio.csv",
            file_type="positions",
            row_count=len(_HOLDINGS),
            imported_at=datetime(2025, 3, 31),
        )
        session.add(batch)
        session.flush()

    # ── Account ────────────────────────────────────────────────────────────────
    acct = session.execute(
        select(Account).where(Account.account_key == _ACCOUNT["account_key"])
    ).scalar_one_or_none()
    if not acct:
        acct = Account(**_ACCOUNT)
        session.add(acct)
        session.flush()

    # ── Instruments, holdings, snapshots ──────────────────────────────────────
    ticker_to_id: dict[str, int] = {}
    for ticker, name, itype, sleeve, is_cash, qty_s, price_s in _HOLDINGS:
        qty = Decimal(qty_s)
        price = Decimal(price_s)
        mv = (qty * price).quantize(Decimal("0.01"))

        inst = session.execute(
            select(Instrument).where(Instrument.ticker == ticker)
        ).scalar_one_or_none()
        if not inst:
            inst = Instrument(
                ticker=ticker, name=name,
                instrument_type=itype, sleeve=sleeve,
                is_cash_equivalent=is_cash,
                needs_unwind=False, aliases=[], bundle_tags=[],
            )
            session.add(inst)
            session.flush()
        ticker_to_id[ticker] = inst.id

        snap = session.execute(
            select(PositionSnapshot).where(
                PositionSnapshot.batch_id == batch.id,
                PositionSnapshot.instrument_id == inst.id,
            )
        ).scalar_one_or_none()
        if not snap:
            session.add(PositionSnapshot(
                batch_id=batch.id, account_id=str(acct.id),
                instrument_id=inst.id, raw_instrument=ticker,
                as_of=today, qty=qty, market_value=mv,
            ))

        holding = session.execute(
            select(Holding).where(Holding.instrument_id == inst.id)
        ).scalar_one_or_none()
        if not holding:
            session.add(Holding(
                instrument_id=inst.id, account_id=str(acct.id),
                qty=qty, market_value=mv, as_of=today,
            ))

    # ── Price history (USD_adj_close) ──────────────────────────────────────────
    n_days = 400
    start_date = today - timedelta(days=n_days)
    trading_days = [
        start_date + timedelta(days=i)
        for i in range(n_days + 1)
        if (start_date + timedelta(days=i)).weekday() < 5
    ]

    for ticker, _, _, _, is_cash, _, price_s in _HOLDINGS:
        if is_cash:
            continue
        inst_id = ticker_to_id[ticker]
        params = _PRICE_PARAMS.get(ticker)
        if not params:
            continue
        start_p, drift, vol = params

        # Check if we already have price history for this instrument
        existing = session.execute(
            select(MarketObservation.id).where(
                MarketObservation.instrument_id == inst_id,
                MarketObservation.unit == "USD_adj_close",
            ).limit(1)
        ).scalar_one_or_none()
        if existing:
            continue

        prices = _gen_prices(start_p, drift, vol, len(trading_days))
        for i, obs_date in enumerate(trading_days):
            session.add(MarketObservation(
                source="yfinance",
                series_id=ticker,
                instrument_id=inst_id,
                observed_date=obs_date,
                value=Decimal(f"{prices[i]:.4f}"),
                unit="USD_adj_close",
                degraded=False,
            ))

    # ── Stock quarterly dividends ──────────────────────────────────────────────
    for ticker, div_s in _STOCK_DIVIDENDS.items():
        inst_id = ticker_to_id.get(ticker)
        if inst_id is None:
            continue
        div = Decimal(div_s)
        # 12 quarters back from today
        for q in range(12):
            months_back = q * 3
            pay_date = date(today.year, today.month, 15) - timedelta(days=months_back * 30)
            existing = session.execute(
                select(MarketObservation.id).where(
                    MarketObservation.instrument_id == inst_id,
                    MarketObservation.unit == "USD_dividend",
                    MarketObservation.observed_date == pay_date,
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(MarketObservation(
                    source="yfinance",
                    series_id=ticker,
                    instrument_id=inst_id,
                    observed_date=pay_date,
                    value=div,
                    unit="USD_dividend",
                    degraded=False,
                ))

    # ── ETF distribution yields ────────────────────────────────────────────────
    for ticker, yield_s in _ETF_DIST_YIELDS.items():
        inst_id = ticker_to_id.get(ticker)
        if inst_id is None:
            continue
        existing = session.execute(
            select(MarketObservation.id).where(
                MarketObservation.instrument_id == inst_id,
                MarketObservation.unit == "distribution_yield",
            ).limit(1)
        ).scalar_one_or_none()
        if not existing:
            session.add(MarketObservation(
                source="yfinance",
                series_id=ticker,
                instrument_id=inst_id,
                observed_date=today,
                value=Decimal(yield_s),
                unit="distribution_yield",
                degraded=False,
            ))

    # ── ETF expense ratios ─────────────────────────────────────────────────────
    for ticker, er_s in _ETF_EXPENSE_RATIOS.items():
        inst_id = ticker_to_id.get(ticker)
        if inst_id is None:
            continue
        existing = session.execute(
            select(Fundamental.id).where(
                Fundamental.instrument_id == inst_id,
                Fundamental.metric == "expense_ratio",
            ).limit(1)
        ).scalar_one_or_none()
        if not existing:
            session.add(Fundamental(
                instrument_id=inst_id,
                period_end=today,
                filed_at=today,
                metric="expense_ratio",
                value=Decimal(er_s),
                unit="ratio",
            ))

    # ── Stock fundamentals ─────────────────────────────────────────────────────
    for ticker, rows in _FUNDAMENTALS.items():
        inst_id = ticker_to_id.get(ticker)
        if inst_id is None:
            continue
        for metric, period_end_s, value_s in rows:
            period_end = date.fromisoformat(period_end_s)
            existing = session.execute(
                select(Fundamental.id).where(
                    Fundamental.instrument_id == inst_id,
                    Fundamental.metric == metric,
                    Fundamental.period_end == period_end,
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(Fundamental(
                    instrument_id=inst_id,
                    period_end=period_end,
                    filed_at=period_end + timedelta(days=45),
                    metric=metric,
                    value=Decimal(value_s),
                    unit=None,
                ))

    # ── FRED macro observations ────────────────────────────────────────────────
    for series_id, obs_list in _MACRO.items():
        for date_s, val_s in obs_list:
            obs_date = date.fromisoformat(date_s)
            existing = session.execute(
                select(MarketObservation.id).where(
                    MarketObservation.series_id == series_id,
                    MarketObservation.observed_date == obs_date,
                )
            ).scalar_one_or_none()
            if not existing:
                session.add(MarketObservation(
                    source="fred",
                    series_id=series_id,
                    instrument_id=None,
                    observed_date=obs_date,
                    value=Decimal(val_s),
                    unit=None,
                    degraded=False,
                ))
