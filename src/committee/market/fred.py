"""FRED data fetcher: 5 macro series + CPIAUCSL YoY derivation."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import requests

from committee.market.throttle import with_backoff

_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Series IDs and their units
FRED_SERIES: dict[str, str] = {
    "T10Y3M": "pct",
    "CPIAUCSL": "index",
    "DTWEXBGS": "index",
    "VIXCLS": "index",
    "BAMLH0A0HYM2": "pct",
}


@dataclass
class FredObs:
    series_id: str
    observed_date: date
    value: Decimal
    unit: str


@with_backoff()
def fetch_fred_series(series_id: str, lookback_years: int = 3) -> list[FredObs]:
    """Fetch recent observations only (default: 3 years back).

    The fredgraph.csv endpoint supports `cosd` / `coed` date filters,
    which keeps payload size from growing unboundedly on repeat calls.
    """
    from datetime import date, timedelta

    start = (date.today() - timedelta(days=lookback_years * 365)).isoformat()
    resp = requests.get(
        _FRED_CSV_URL,
        params={"id": series_id, "cosd": start},
        timeout=30,
    )
    resp.raise_for_status()
    return _parse_fred_csv(series_id, resp.text)


def _parse_fred_csv(series_id: str, text: str) -> list[FredObs]:
    unit = FRED_SERIES.get(series_id, "")
    reader = csv.reader(io.StringIO(text))
    result = []
    for i, row in enumerate(reader):
        if i == 0:
            continue  # DATE,series_id header row
        if len(row) < 2:
            continue
        date_str, val_str = row[0].strip(), row[1].strip()
        if not date_str or val_str in (".", "", "ND"):
            continue
        try:
            obs_date = date.fromisoformat(date_str)
            value = Decimal(val_str)
        except (ValueError, InvalidOperation):
            continue
        result.append(FredObs(series_id=series_id, observed_date=obs_date, value=value, unit=unit))
    return result


def compute_cpi_yoy(obs: list[FredObs]) -> list[FredObs]:
    """Derive CPIAUCSL year-over-year (%) from monthly raw observations."""
    series: dict[date, Decimal] = {o.observed_date: o.value for o in obs}
    result = []
    for d in sorted(series):
        try:
            d_prev = date(d.year - 1, d.month, d.day)
        except ValueError:
            continue
        prev_val = series.get(d_prev)
        if prev_val is None or prev_val == 0:
            continue
        yoy = (series[d] - prev_val) / prev_val * Decimal("100")
        result.append(
            FredObs(
                series_id="CPIAUCSL_YOY",
                observed_date=d,
                value=yoy.quantize(Decimal("0.0001")),
                unit="pct",
            )
        )
    return result


def fetch_all_fred() -> dict[str, list[FredObs]]:
    """Fetch all configured FRED series in parallel, then derive CPIAUCSL_YOY."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, list[FredObs]] = {}
    with ThreadPoolExecutor(max_workers=len(FRED_SERIES)) as pool:
        futures = {pool.submit(fetch_fred_series, sid): sid for sid in FRED_SERIES}
        for fut in as_completed(futures):
            sid = futures[fut]
            results[sid] = fut.result()  # raises on error, propagates to caller

    results["CPIAUCSL_YOY"] = compute_cpi_yoy(results.get("CPIAUCSL", []))
    return results
