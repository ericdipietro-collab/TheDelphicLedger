"""Tests for committee.rebalancer.export — Fidelity and Schwab CSV export."""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest

from committee.core.types import TradeProposal
from committee.rebalancer.export import export_fidelity_csv, export_schwab_csv

FIXTURES_DIR = Path(__file__).parent / "fixtures"

PROPOSALS = [
    TradeProposal(
        instrument_id=1,
        account_id="ACCT-001",
        direction="buy",
        qty=Decimal("10"),
        estimated_value=Decimal("1500.0000"),
        rationale_tags=["drift"],
        oracle_score=0.75,
        tax_note=None,
    ),
    TradeProposal(
        instrument_id=2,
        account_id="ACCT-001",
        direction="sell",
        qty=Decimal("5"),
        estimated_value=Decimal("-600.0000"),
        rationale_tags=["drift"],
        oracle_score=-0.40,
        tax_note=None,
    ),
]
TICKER_MAP: dict[int, str] = {1: "AAPL", 2: "BND"}
ORACLE_ID = "value_purist"
ACCOUNT_MAP: dict[str, str] = {"ACCT-001": "ACCT-001"}


def _parse_csv(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


# ── Fidelity ──────────────────────────────────────────────────────────────────


def test_fidelity_export_columns() -> None:
    csv_str, _ = export_fidelity_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    assert rows, "Expected at least one data row"
    expected_cols = {"Account Number", "Symbol", "Action", "Quantity", "Order Type", "Duration", "Memo"}
    assert set(rows[0].keys()) == expected_cols


def test_fidelity_buy_direction() -> None:
    csv_str, _ = export_fidelity_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    buy_rows = [r for r in rows if r["Symbol"] == "AAPL"]
    assert len(buy_rows) == 1
    row = buy_rows[0]
    assert row["Action"] == "Buy"
    assert row["Quantity"] == "10"
    assert "value_purist" in row["Memo"]
    assert "drift" in row["Memo"]


def test_fidelity_sell_direction() -> None:
    csv_str, _ = export_fidelity_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    sell_rows = [r for r in rows if r["Symbol"] == "BND"]
    assert len(sell_rows) == 1
    row = sell_rows[0]
    assert row["Action"] == "Sell"
    assert row["Quantity"] == "5"


# ── Schwab ────────────────────────────────────────────────────────────────────


def test_schwab_export_columns() -> None:
    csv_str, _ = export_schwab_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    assert rows, "Expected at least one data row"
    expected_cols = {"Account", "Symbol", "Action", "Quantity", "Order Type", "Duration", "Description"}
    assert set(rows[0].keys()) == expected_cols


def test_schwab_buy_direction() -> None:
    csv_str, _ = export_schwab_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    buy_rows = [r for r in rows if r["Symbol"] == "AAPL"]
    assert len(buy_rows) == 1
    assert buy_rows[0]["Action"] == "Buy to Open"


def test_schwab_sell_direction() -> None:
    csv_str, _ = export_schwab_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    sell_rows = [r for r in rows if r["Symbol"] == "BND"]
    assert len(sell_rows) == 1
    assert sell_rows[0]["Action"] == "Sell to Close"


# ── Missing account mapping ───────────────────────────────────────────────────


def test_missing_account_mapping_warns() -> None:
    csv_str, warnings = export_fidelity_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, {})
    assert warnings, "Expected at least one warning"
    assert any("ACCT-001" in w for w in warnings)
    # Output should still be produced (graceful fallback)
    rows = _parse_csv(csv_str)
    assert len(rows) == 2
    # Fallback: account_key used as account number
    assert all(r["Account Number"] == "ACCT-001" for r in rows)


# ── CASH skip ─────────────────────────────────────────────────────────────────


def test_cash_ticker_skipped() -> None:
    proposals_with_cash = [
        *PROPOSALS,
        TradeProposal(
            instrument_id=99,
            account_id="ACCT-001",
            direction="buy",
            qty=Decimal("1000"),
            estimated_value=Decimal("1000"),
            rationale_tags=[],
            oracle_score=None,
            tax_note=None,
        ),
    ]
    ticker_map_with_cash = {**TICKER_MAP, 99: "CASH"}
    csv_str, _ = export_fidelity_csv(proposals_with_cash, ticker_map_with_cash, ORACLE_ID, ACCOUNT_MAP)
    rows = _parse_csv(csv_str)
    symbols = {r["Symbol"] for r in rows}
    assert "CASH" not in symbols
    assert len(rows) == 2  # only AAPL and BND


# ── Golden file tests ─────────────────────────────────────────────────────────


def test_fidelity_golden() -> None:
    golden_path = FIXTURES_DIR / "golden_fidelity.csv"
    assert golden_path.exists(), f"Golden file missing: {golden_path}"
    # Open with newline='' to preserve \r\n exactly as written
    with golden_path.open(encoding="utf-8", newline="") as fh:
        expected = fh.read()
    actual, _ = export_fidelity_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    assert actual == expected


def test_schwab_golden() -> None:
    golden_path = FIXTURES_DIR / "golden_schwab.csv"
    assert golden_path.exists(), f"Golden file missing: {golden_path}"
    # Open with newline='' to preserve \r\n exactly as written
    with golden_path.open(encoding="utf-8", newline="") as fh:
        expected = fh.read()
    actual, _ = export_schwab_csv(PROPOSALS, TICKER_MAP, ORACLE_ID, ACCOUNT_MAP)
    assert actual == expected
