"""Price/dividend adapters: Tiingo (requests, API key) and yfinance (no key)."""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from decimal import Decimal

import requests

from committee.market.adapter import PriceObs
from committee.market.throttle import with_backoff

_TIINGO_BASE = "https://api.tiingo.com"

# Thread-local storage for per-thread Tiingo sessions (requests.Session isn't thread-safe)
_tls = threading.local()


class TiingoAdapter:
    source_name = "tiingo"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._session = requests.Session()
        self._session.headers.update(
            {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        )

    def _thread_session(self) -> requests.Session:
        if not hasattr(_tls, "tiingo_session"):
            _tls.tiingo_session = requests.Session()
            _tls.tiingo_session.headers.update(
                {"Authorization": f"Token {self._api_key}", "Content-Type": "application/json"}
            )
        return _tls.tiingo_session

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

    def _fetch_eod_threadsafe(self, ticker: str, start: date, end: date) -> list[PriceObs]:
        """Like fetch_eod but uses a thread-local session."""
        sess = self._thread_session()
        resp = sess.get(
            f"{_TIINGO_BASE}/tiingo/daily/{ticker}/prices",
            params={
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "resampleFreq": "daily",
            },
            timeout=30,
        )
        resp.raise_for_status()
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

    def fetch_eod_batch(
        self, tickers: list[str], start: date, end: date, max_workers: int = 5
    ) -> dict[str, list[PriceObs] | Exception]:
        """Fetch multiple tickers concurrently. Returns ticker → results or Exception."""
        if not tickers:
            return {}
        result: dict[str, list[PriceObs] | Exception] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._fetch_eod_threadsafe, t, start, end): t for t in tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                exc = future.exception()
                result[ticker] = exc if exc is not None else future.result()
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

    def fetch_eod_batch(
        self, tickers: list[str], start: date, end: date, max_workers: int = 5
    ) -> dict[str, list[PriceObs] | Exception]:
        """Fetch all tickers in one yf.download() call (uses threads internally).

        Returns ticker → list[PriceObs] or Exception.
        """
        import logging

        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        if not tickers:
            return {}

        result: dict[str, list[PriceObs] | Exception] = {t: [] for t in tickers}

        if len(tickers) == 1:
            try:
                result[tickers[0]] = self.fetch_eod(tickers[0], start, end)
            except Exception as exc:
                result[tickers[0]] = exc
            return result

        # yf.download with multiple tickers uses threads internally and returns
        # a MultiIndex DataFrame: columns are (field, ticker) — e.g. ('Close', 'AAPL')
        data = yf.download(
            tickers=tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
            group_by="column",  # columns = (field, ticker), rows = dates
            threads=True,
        )

        if data.empty:
            return result

        for ticker in tickers:
            try:
                obs_list: list[PriceObs] = []
                for idx, row in data.iterrows():
                    # MultiIndex column access: ('Close', ticker)
                    close_raw = row.get(("Close", ticker))
                    if close_raw is None:
                        continue
                    import math
                    if isinstance(close_raw, float) and math.isnan(close_raw):
                        continue
                    close_f = float(close_raw)
                    if close_f == 0.0:
                        continue
                    div_raw = row.get(("Dividends", ticker), 0) or 0
                    div_f = 0.0 if (isinstance(div_raw, float) and math.isnan(div_raw)) else float(div_raw)
                    obs_list.append(
                        PriceObs(
                            observed_date=idx.date(),
                            adj_close=Decimal(str(close_f)),
                            dividend=Decimal(str(div_f)),
                        )
                    )
                result[ticker] = obs_list
            except Exception as exc:
                result[ticker] = exc

        return result


def get_price_adapter() -> TiingoAdapter | YfinanceAdapter:
    """Return the appropriate adapter based on available credentials."""
    api_key = os.environ.get("TIINGO_API_KEY")
    if api_key:
        return TiingoAdapter(api_key)
    return YfinanceAdapter()
