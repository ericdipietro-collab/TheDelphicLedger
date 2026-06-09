"""EDGAR client: CIK lookup, companyfacts XBRL, submissions + 8-K item flags.

Respects the SEC's ~10 req/s courtesy limit (0.11s min gap between requests).
Set EDGAR_USER_AGENT env var to override the default User-Agent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import requests

from committee.market.throttle import with_backoff

_EDGAR_BASE = "https://data.sec.gov"
_CIK_MAP_URL = "https://www.sec.gov/files/company_tickers.json"

_MIN_INTERVAL = 0.11  # ~9 req/s, under the 10 req/s SEC courtesy limit
_last_edgar_request: float = 0.0


def _edgar_wait() -> None:
    global _last_edgar_request
    elapsed = time.monotonic() - _last_edgar_request
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_edgar_request = time.monotonic()


def _edgar_headers() -> dict[str, str]:
    ua = os.environ.get("EDGAR_USER_AGENT", "DelphicLedger contact@example.com")
    return {"User-Agent": ua, "Accept": "application/json"}


# ── CIK lookup ─────────────────────────────────────────────────────────────────

_CIK_CACHE: dict[str, int] | None = None


@with_backoff(max_retries=3, base_delay=1.0)
def _load_cik_map() -> dict[str, int]:
    """Fetch and in-process-cache the SEC ticker→CIK bulk mapping file."""
    global _CIK_CACHE
    if _CIK_CACHE is not None:
        return _CIK_CACHE
    _edgar_wait()
    resp = requests.get(_CIK_MAP_URL, headers=_edgar_headers(), timeout=30)
    resp.raise_for_status()
    raw = json.loads(resp.text)
    mapping: dict[str, int] = {}
    for entry in raw.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = int(entry.get("cik_str", 0))
        if ticker and cik:
            mapping[ticker] = cik
    _CIK_CACHE = mapping
    return mapping


def lookup_cik(ticker: str) -> int | None:
    """Return the SEC CIK for a ticker, or None if not found."""
    return _load_cik_map().get(ticker.upper())


def clear_cik_cache() -> None:
    """Clear the in-process CIK map cache (for testing)."""
    global _CIK_CACHE
    _CIK_CACHE = None


# ── Companyfacts XBRL ─────────────────────────────────────────────────────────

# Preferred XBRL concept names per metric (first match wins)
_METRIC_CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "equity": ["StockholdersEquity", "StockholdersEquityAttributableToParent"],
    "total_debt": ["LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "dividends_paid": ["Dividends", "DividendsPaidCommonStockCash", "PaymentsOfDividends"],
    "gross_profit": ["GrossProfit"],
    "net_income": ["NetIncomeLoss", "NetIncome", "ProfitLoss"],
    "total_assets": ["Assets"],
    "operating_income": ["OperatingIncomeLoss"],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfSharesOutstandingDiluted",
    ],
}


@dataclass
class FundamentalObs:
    metric: str
    period_end: date
    filed_at: date | None
    value: Decimal
    unit: str | None = None


@with_backoff(max_retries=3, base_delay=1.0)
def fetch_companyfacts(cik: int) -> list[FundamentalObs]:
    """Fetch annual XBRL facts for a given CIK."""
    cik_str = f"CIK{cik:010d}"
    url = f"{_EDGAR_BASE}/api/xbrl/companyfacts/{cik_str}.json"
    _edgar_wait()
    resp = requests.get(url, headers=_edgar_headers(), timeout=60)
    resp.raise_for_status()
    return _parse_companyfacts(json.loads(resp.text))


def _parse_companyfacts(data: dict) -> list[FundamentalObs]:
    us_gaap = data.get("facts", {}).get("us-gaap", {})
    results: list[FundamentalObs] = []
    for metric, concepts in _METRIC_CONCEPTS.items():
        for obs, unit in _extract_annual(us_gaap, concepts):
            try:
                period_end = date.fromisoformat(obs["end"])
                value = Decimal(str(obs["val"]))  # val is int in JSON; Decimal(str) avoids float
                filed_str = obs.get("filed")
                filed_at = date.fromisoformat(filed_str) if filed_str else None
            except (KeyError, ValueError):
                continue
            results.append(
                FundamentalObs(
                    metric=metric,
                    period_end=period_end,
                    filed_at=filed_at,
                    value=value,
                    unit=unit,
                )
            )
    return results


def _extract_annual(us_gaap: dict, concepts: list[str]) -> list[tuple[dict, str]]:
    """Try each XBRL concept in order; return annual 10-K observations from the first hit."""
    for concept in concepts:
        node = us_gaap.get(concept)
        if not node:
            continue
        for unit_key, unit_obs in node.get("units", {}).items():
            annual = [
                o
                for o in unit_obs
                if o.get("form") == "10-K" and o.get("val") is not None and o.get("end")
            ]
            if annual:
                return [(o, unit_key) for o in annual]
    return []


# ── Submissions + 8-K item flags ──────────────────────────────────────────────

_TRACKED_8K_ITEMS = frozenset(["4.01", "4.02", "5.02", "2.06"])


@dataclass
class EightKFlag:
    item_code: str
    filing_date: date


@with_backoff(max_retries=3, base_delay=1.0)
def fetch_submissions(cik: int) -> list[EightKFlag]:
    """Fetch recent 8-K filings and return flags for tracked item codes."""
    cik_str = f"CIK{cik:010d}"
    url = f"{_EDGAR_BASE}/submissions/{cik_str}.json"
    _edgar_wait()
    resp = requests.get(url, headers=_edgar_headers(), timeout=30)
    resp.raise_for_status()
    return _parse_8k_flags(json.loads(resp.text))


def _parse_8k_flags(data: dict) -> list[EightKFlag]:
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    items_list = recent.get("items", [])
    results = []
    for form, date_str, items_str in zip(forms, dates, items_list, strict=False):
        if form != "8-K":
            continue
        try:
            filing_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        for code in (items_str or "").split(","):
            code = code.strip()
            if code in _TRACKED_8K_ITEMS:
                results.append(EightKFlag(item_code=code, filing_date=filing_date))
    return results


# ── High-level batch fetch ────────────────────────────────────────────────────

@dataclass
class EdgarResult:
    ticker: str
    instrument_id: int
    obs: list[FundamentalObs]
    error: str | None = None


def fetch_edgar_for_ticker(ticker: str, instrument_id: int) -> EdgarResult:
    """Fetch annual fundamentals for one ticker. Returns EdgarResult (error is set on failure)."""
    cik = lookup_cik(ticker)
    if cik is None:
        return EdgarResult(ticker=ticker, instrument_id=instrument_id, obs=[], error="no_cik")
    try:
        obs = fetch_companyfacts(cik)
        return EdgarResult(ticker=ticker, instrument_id=instrument_id, obs=obs)
    except Exception as e:
        return EdgarResult(ticker=ticker, instrument_id=instrument_id, obs=[], error=str(e))


def fetch_all_edgar(instruments: list[tuple[str, int]]) -> list[EdgarResult]:
    """Fetch EDGAR fundamentals for a list of (ticker, instrument_id) pairs.

    Uses a small thread pool — SEC courtesy limit is ~10 req/s and _edgar_wait()
    enforces the inter-request gap, so concurrency is bounded to 3 workers.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[EdgarResult] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {
            pool.submit(fetch_edgar_for_ticker, ticker, iid): (ticker, iid)
            for ticker, iid in instruments
        }
        for fut in as_completed(futures):
            results.append(fut.result())
    return results
