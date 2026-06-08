"""Instrument type/class classification from available metadata.

Never fabricates values — returns "unclassified" when the answer
cannot be determined deterministically from the available inputs.
"""

from __future__ import annotations

import re

# OpenFIGI securityType values that map to our type enum.
_FIGI_TYPE_MAP: dict[str, str] = {
    "Common Stock": "stock",
    "Preferred Stock": "stock",
    "ETP": "etf",                     # Exchange-Traded Product
    "ETF": "etf",
    "Open-End Fund": "mutual_fund",
    "Money Market": "cash",
    "Government": "mutual_fund",       # gov money market funds
}

_BOND_KEYWORDS = re.compile(
    r"\b(bond|income|treasury|fixed\s+income|note|credit|yield|duration"
    r"|municipal|muni|gilt|aggregate|tips)\b",
    re.IGNORECASE,
)
_EQUITY_KEYWORDS = re.compile(
    r"\b(equity|stock|growth|value|dividend|large[- ]cap|small[- ]cap|mid[- ]cap"
    r"|s&p|nasdaq|russell|total\s+market|index)\b",
    re.IGNORECASE,
)
_FUND_KEYWORDS = re.compile(r"\b(fund|etf|portfolio|trust)\b", re.IGNORECASE)


def classify_from_figi_type(figi_security_type: str) -> str:
    """Map an OpenFIGI securityType string to our instrument_type enum."""
    return _FIGI_TYPE_MAP.get(figi_security_type, "unclassified")


def classify_from_name(name: str | None) -> str:
    """Heuristic type classification from instrument name.

    Only returns a non-unclassified result when the name strongly implies a
    specific type. Never guesses for ambiguous names.
    """
    if not name:
        return "unclassified"
    n = name.strip()
    if "ETF" in n.upper():
        return "etf"
    if re.search(r"fund\b", n, re.IGNORECASE) and "ETF" not in n.upper():
        return "mutual_fund"
    return "unclassified"


def infer_asset_class(instrument_type: str, name: str | None) -> str:
    """Infer asset_class from instrument_type and name."""
    if instrument_type == "cash":
        return "cash"
    if name and _BOND_KEYWORDS.search(name):
        return "fixed_income"
    if instrument_type in ("stock", "etf", "mutual_fund") and name and _EQUITY_KEYWORDS.search(name):
        return "equity"
    if instrument_type == "stock":
        return "equity"
    return "unclassified"


def needs_unwind_flag(instrument_type: str) -> bool:
    """Individual equities are flagged for the unwind path in funds-only mode."""
    return instrument_type == "stock"
