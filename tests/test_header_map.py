from __future__ import annotations

from committee.ingest.header_map import propose_mapping


def test_exact_symbol_maps() -> None:
    proposals = propose_mapping(["Symbol", "Description", "Qty", "Market Value"])
    by_source = {p.source_col: p for p in proposals}
    assert by_source["Symbol"].canonical_field == "symbol"
    assert by_source["Symbol"].method == "exact"


def test_fuzzy_shares_maps_to_qty() -> None:
    proposals = propose_mapping(["Shares", "Fund Name", "Share Price", "Total Value"])
    by_source = {p.source_col: p for p in proposals}
    qty_p = by_source["Shares"]
    assert qty_p.canonical_field == "qty"


def test_total_value_maps_to_market_value() -> None:
    proposals = propose_mapping(["Fund Name", "Ticker Symbol", "Shares", "Share Price", "Total Value"])
    by_source = {p.source_col: p for p in proposals}
    assert by_source["Total Value"].canonical_field == "market_value"


def test_trade_date_maps() -> None:
    proposals = propose_mapping(["Trade Date", "Transaction Type", "Amount"])
    by_source = {p.source_col: p for p in proposals}
    assert by_source["Trade Date"].canonical_field == "trade_date"


def test_no_duplicate_canonical_fields() -> None:
    headers = ["Symbol", "Ticker", "Qty", "Quantity", "Market Value"]
    proposals = propose_mapping(headers)
    auto_mapped = [p.canonical_field for p in proposals if p.canonical_field is not None and p.method != "fuzzy_pending"]
    assert len(auto_mapped) == len(set(auto_mapped)), "Duplicate canonical fields in proposals"


def test_unmatched_column() -> None:
    proposals = propose_mapping(["XYZ_UNKNOWN_FIELD"])
    assert proposals[0].canonical_field is None
    assert proposals[0].method == "unmatched"
