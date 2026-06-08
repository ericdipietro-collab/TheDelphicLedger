"""OpenFIGI lookup client.

The actual HTTP call is isolated in `live_figi_lookup` so callers can
inject a stub in tests — CI must never touch the network (Invariant F).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import requests

_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
_TIMEOUT_S = 10


@dataclass
class FigiResult:
    figi: str
    ticker: str
    name: str
    security_type: str
    exchange: str | None = None


def live_figi_lookup(ticker: str) -> FigiResult | None:
    """Hit the OpenFIGI API and return the best match, or None.

    Anonymous access: 25 requests/minute limit. Callers are responsible for
    rate-limiting. Tests must NOT call this — inject a stub instead.
    """
    try:
        resp = requests.post(
            _OPENFIGI_URL,
            json=[{"idType": "TICKER", "idValue": ticker}],
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
    except (requests.RequestException, json.JSONDecodeError):
        return None

    if not body or not isinstance(body, list):
        return None
    first = body[0]
    if "data" not in first or not first["data"]:
        return None

    hit = first["data"][0]
    return FigiResult(
        figi=hit.get("figi", ""),
        ticker=hit.get("ticker", ticker),
        name=hit.get("name", ""),
        security_type=hit.get("securityType", ""),
        exchange=hit.get("exchCode"),
    )
