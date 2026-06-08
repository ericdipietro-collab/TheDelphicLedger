from __future__ import annotations

import pytest

from committee.ingest.txn_canon import canonicalize_type


@pytest.mark.parametrize("raw,expected", [
    ("Buy", "buy"),
    ("buy", "buy"),
    ("YOU BOUGHT", "buy"),
    ("Purchase", "buy"),
    ("Sell", "sell"),
    ("YOU SOLD", "sell"),
    ("Dividend", "dividend"),
    ("Cash Dividend", "dividend"),
    ("DIVIDEND RECEIVED", "dividend"),
    ("Interest", "interest"),
    ("INTEREST EARNED", "interest"),
    ("Reinvest Shares", "reinvest"),
    ("REINVESTMENT", "reinvest"),
    ("Dividend Reinvestment", "reinvest"),
    ("Sweep", "sweep"),
    ("fee", "fee"),
    ("Commission", "fee"),
    ("Transfer In", "transfer_in"),
    ("ACATS In", "transfer_in"),
    ("Transfer Out", "transfer_out"),
    ("Stock Split", "split"),
    ("Return of Capital", "return_of_capital"),
])
def test_known_types(raw: str, expected: str) -> None:
    assert canonicalize_type(raw) == expected


def test_unknown_type_returns_none() -> None:
    assert canonicalize_type("Transfer of securities in") is None


def test_extra_aliases_override() -> None:
    aliases = {"Transfer of securities in": "transfer_in"}
    assert canonicalize_type("Transfer of securities in", aliases) == "transfer_in"


def test_extra_aliases_case_insensitive() -> None:
    aliases = {"DIRECT DEBIT": "transfer_out"}
    assert canonicalize_type("direct debit", aliases) == "transfer_out"
