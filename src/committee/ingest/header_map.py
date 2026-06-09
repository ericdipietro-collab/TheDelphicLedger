"""Map raw broker column headers to canonical field names."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz

SYNONYMS: dict[str, list[str]] = {
    # positions
    "symbol": ["symbol", "ticker", "ticker symbol", "security", "cusip"],
    "name": ["name", "description", "security name", "fund name", "investment name", "security description", "investment"],
    "qty": ["quantity", "qty", "shares", "units", "# shares", "shares/units"],
    "price": ["price", "last price", "market price", "close price", "share price"],
    "market_value": ["market value", "value", "current value", "total value", "market val"],
    "cost_basis": ["cost basis", "total cost", "cost", "adjusted cost basis", "book value"],
    "as_of": ["as of", "date", "price as of", "value date"],
    # transactions
    "trade_date": ["trade date", "date", "transaction date", "settlement date", "trade"],
    "settle_date": ["settlement date", "settle date", "settled"],
    "raw_type": ["action", "type", "transaction type", "activity", "transaction", "trans type", "activity type"],
    "distribution_yield": ["div yld", "dividend yield", "yield", "distribution yield", "div yield", "sec yld"],
    "asset_class": ["asset type", "asset class", "security type", "type of investment", "product type"],
    "amount": ["amount", "net amount", "total", "proceeds", "value"],
    "fees": ["fees", "commission", "fee", "charges"],
}

# Reverse lookup: synonym -> canonical
_REVERSE: dict[str, str] = {}
for _canonical, _syns in SYNONYMS.items():
    for _s in _syns:
        _REVERSE[_s.lower()] = _canonical

AUTO_THRESHOLD = 90
PENDING_THRESHOLD = 70


@dataclass
class MappingProposal:
    source_col: str
    canonical_field: str | None
    method: str  # "exact" | "fuzzy_auto" | "fuzzy_pending" | "unmatched"
    score: float | None


def propose_mapping(headers: list[str]) -> list[MappingProposal]:
    """Propose canonical→source column mappings for a list of raw headers."""
    proposals: list[MappingProposal] = []
    used_canonical: set[str] = set()

    for header in headers:
        normalized = header.strip().lower()

        # Exact match
        if normalized in _REVERSE:
            canonical = _REVERSE[normalized]
            if canonical not in used_canonical:
                proposals.append(
                    MappingProposal(header, canonical, "exact", 100.0)
                )
                used_canonical.add(canonical)
                continue

        # Fuzzy match against all synonyms
        best_score = 0.0
        best_canonical: str | None = None
        for syn, canonical in _REVERSE.items():
            if canonical in used_canonical:
                continue
            score = fuzz.token_sort_ratio(normalized, syn)
            if score > best_score:
                best_score = score
                best_canonical = canonical

        if best_score >= AUTO_THRESHOLD and best_canonical is not None:
            proposals.append(
                MappingProposal(header, best_canonical, "fuzzy_auto", best_score)
            )
            used_canonical.add(best_canonical)
        elif best_score >= PENDING_THRESHOLD and best_canonical is not None:
            proposals.append(
                MappingProposal(header, best_canonical, "fuzzy_pending", best_score)
            )
            # Don't mark used — human must confirm
        else:
            proposals.append(MappingProposal(header, None, "unmatched", best_score or None))

    return proposals


def apply_column_map(
    column_map: dict[str, str | None],
    row: list[str],
    headers: list[str],
) -> dict[str, str]:
    """Apply a finalized column_map to a data row. Returns {canonical: raw_value}."""
    header_index = {h: i for i, h in enumerate(headers)}
    result: dict[str, str] = {}
    for source_col, canonical in column_map.items():
        if canonical is None:
            continue
        idx = header_index.get(source_col)
        if idx is not None and idx < len(row):
            result[canonical] = row[idx].strip()
    return result
