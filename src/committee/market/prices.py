"""Price/dividend adapters: Tiingo (requests, API key) and yfinance (no key)."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import requests

from committee.market.adapter import PriceObs
from committee.market.throttle import with_backoff

_TIINGO_BASE = "https://api.tiingo.com"


class TiingoAdapter:
    source_name = "tiingo"

    def __init__(self, api_key: str) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        )

    @with_backoff()
    def fetch_eod(self, ticker: str, start: date, end: date) -> list[PriceObs]:
        resp = self._session.get(
            f"{_TIINGO_BASE}/tiingo/daily/{ticker}/prices",
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "resampleFreq": "daily",
            },
            timeout=30,
        )
        resp.raise_for_status()
        # parse_float=Decimal: no float ever materialises from JSON (Invariant E)
        data: list[dict] = json.loads(resp.text, parse_float=Decimal)
        result = []
        for row in data:
            d_str = str(row["date"])[:10]
            adj_close = Decimal(str(row.get("adjClose") or row.get("close") or "0"))
            dividend = Decimal(str(row.get("divCash") or "0"))
            result.append(
                PriceObs(
                    observed_date=date.fromisoformat(d_str),
                    adj_close=adj_close,
                    dividend=dividend,
                )
            )
        return result


class YfinanceAdapter:
    """Fallback adapter using yfinance (no API key required)."""

    source_name = "yfinance"

    def fetch_eod(self, ticker: str, start: date, end: date) -> list[PriceObs]:
        import logging

        import yfinance as yf  # imported lazily: optional dep

        # Suppress yfinance's own stderr/logger chatter (404s, delisted warnings).
        # Errors still propagate as exceptions — we just don't want them in the
        # server console for expected cases like preferred stock tickers.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        # .history() returns a flat DataFrame regardless of yfinance version.
        # yf.download() with newer versions emits MultiIndex columns that make
        # row["Close"] return a one-element Series, which breaks truthiness tests.
        hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
        result = []
        for idx, row in hist.iterrows():
            close_raw = row.get("Close")
            if close_raw is None:
                continue
            close_f = float(close_raw.iloc[0] if hasattr(close_raw, "iloc") else close_raw)
            if close_f == 0.0:
                continue
            div_raw = row.get("Dividends", 0)
            div_f = float(div_raw.iloc[0] if hasattr(div_raw, "iloc") else div_raw)
            result.append(
                PriceObs(
                    observed_date=idx.date(),
                    adj_close=Decimal(str(close_f)),
                    dividend=Decimal(str(div_f)),
                )
            )
        return result


def get_price_adapter() -> TiingoAdapter | YfinanceAdapter:
    """Return the appropriate adapter based on available credentials."""
    api_key = os.environ.get("TIINGO_API_KEY")
    if api_key:
        return TiingoAdapter(api_key)
    return YfinanceAdapter()
