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
        import yfinance as yf  # imported lazily: optional dep

        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
        result = []
        for idx, row in df.iterrows():
            close_val = row.get("Close") or row.get("Adj Close")
            if close_val is None:
                continue
            result.append(
                PriceObs(
                    observed_date=idx.date(),
                    # Decimal(str(...)) avoids float contamination from pandas
                    adj_close=Decimal(str(float(close_val))),
                    dividend=Decimal("0"),
                )
            )
        return result


def get_price_adapter() -> TiingoAdapter | YfinanceAdapter:
    """Return the appropriate adapter based on available credentials."""
    api_key = os.environ.get("TIINGO_API_KEY")
    if api_key:
        return TiingoAdapter(api_key)
    return YfinanceAdapter()
