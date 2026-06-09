from __future__ import annotations

from committee.ingest.preclean import is_junk_row, preclean


def test_blank_row_is_junk() -> None:
    assert is_junk_row(["", "", ""])


def test_account_total_is_junk() -> None:
    assert is_junk_row(["Account Total", "100", "200"])


def test_total_prefix_is_junk() -> None:
    assert is_junk_row(["Total", "", ""])


def test_disclaimer_is_junk() -> None:
    assert is_junk_row(["Brokerage services and products: Not FDIC-Insured", "", ""])


def test_data_row_not_junk() -> None:
    assert not is_junk_row(["AAPL", "Apple Inc", "100", "189.30"])


def test_brk_slash_not_junk() -> None:
    assert not is_junk_row(["BRK/B", "Berkshire Hathaway", "50", "403.50"])


def test_preclean_strips_preheader_and_junk() -> None:
    rows = [
        ["Positions for account XYZ as of 06/07/2026"],
        [],
        ["Symbol", "Description", "Qty", "Market Value"],
        ["AAPL", "Apple Inc", "100", "$18930"],
        ["Account Total", "", "", "$18930"],
        ["Not FDIC Insured"],
    ]
    headers, data = preclean(rows)
    assert headers == ["Symbol", "Description", "Qty", "Market Value"]
    assert len(data) == 1
    assert data[0][0] == "AAPL"


def test_preclean_no_preheader() -> None:
    rows = [
        ["Symbol", "Description", "Qty", "Market Value"],
        ["VTI", "Vanguard ETF", "200", "$48030"],
    ]
    headers, data = preclean(rows)
    assert headers[0] == "Symbol"
    assert len(data) == 1


def test_positions_total_is_junk() -> None:
    assert is_junk_row(["Positions Total", "", ""])


def test_positions_total_with_values_is_junk() -> None:
    assert is_junk_row(["Positions Total", "0.00", "$47,832.00", "$47,832.00", "", "", ""])


def test_cash_equivalents_total_is_junk() -> None:
    assert is_junk_row(["Cash & Cash Equivalents", "0.00", "$13,581.00"])
