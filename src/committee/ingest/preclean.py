"""Remove junk rows from raw CSV data before header mapping."""

from __future__ import annotations

import re

_JUNK_PATTERNS = [
    re.compile(r"^\s*$"),                          # blank row
    re.compile(r"account\s+total", re.IGNORECASE),
    re.compile(r"positions\s+total", re.IGNORECASE),    # Schwab summary row
    re.compile(r"cash\s+&\s+cash\s+equivalents\b", re.IGNORECASE),  # Schwab cash subtotal
    re.compile(r"^\s*total\b", re.IGNORECASE),
    re.compile(r"^\s*grand\s+total", re.IGNORECASE),
    re.compile(r"brokerage\s+services", re.IGNORECASE),
    re.compile(r"schwab\s+one", re.IGNORECASE),
    re.compile(r"not\s+fdic\s+insured", re.IGNORECASE),
    re.compile(r"investments\s+are\s+not", re.IGNORECASE),
    re.compile(r"^\s*\*{2,}", re.IGNORECASE),      # rows of asterisks
    re.compile(r"^\s*-{3,}", re.IGNORECASE),       # rows of dashes
]


def is_junk_row(row: list[str]) -> bool:
    """Return True if this row should be dropped before processing."""
    first_cell = row[0] if row else ""
    joined = " ".join(row)
    for pattern in _JUNK_PATTERNS:
        if pattern.search(first_cell) or pattern.search(joined):
            return True
    # Row with only one non-empty cell that doesn't look like data
    non_empty = [c for c in row if c.strip()]
    return (
        len(non_empty) == 1
        and not re.match(r"^[A-Z]{1,5}[\./]?[A-Z]?$", non_empty[0])
        and len(non_empty[0]) > 30
    )


def find_header_row(rows: list[list[str]], min_fields: int = 3) -> int:
    """Return the index of the most likely header row.

    Heuristic: first row with >= min_fields non-empty cells that contains at
    least one word matching a known canonical synonym.
    Falls back to the first row with >= min_fields non-empty cells.
    """
    from committee.ingest.header_map import SYNONYMS

    all_known = {syn for syns in SYNONYMS.values() for syn in syns}

    first_dense = -1
    for i, row in enumerate(rows):
        non_empty = [c.strip() for c in row if c.strip()]
        if len(non_empty) < min_fields:
            continue
        if first_dense == -1:
            first_dense = i
        normalized = {cell.strip().lower() for cell in non_empty}
        if normalized & all_known:
            return i

    return first_dense


def preclean(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Strip pre-header lines, detect header, remove junk data rows.

    Returns (headers, data_rows).
    """
    header_idx = find_header_row(rows)
    if header_idx < 0:
        return [], []

    headers = [c.strip() for c in rows[header_idx]]
    data_rows: list[list[str]] = []
    for row in rows[header_idx + 1 :]:
        # Pad short rows
        padded = row + [""] * (len(headers) - len(row))
        padded = padded[: len(headers)]
        if not is_junk_row(padded):
            data_rows.append(padded)

    return headers, data_rows
