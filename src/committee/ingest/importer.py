"""Core import pipeline: read CSV → preclean → map → parse → ORM rows."""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dateutil.parser import parse as parse_date
from pydantic import BaseModel, ConfigDict

from committee.ingest.file_type import detect_file_type
from committee.ingest.header_map import MappingProposal, apply_column_map, propose_mapping
from committee.ingest.preclean import preclean
from committee.ingest.template import MappingTemplate, compute_fingerprint, load_template
from committee.ingest.txn_canon import canonicalize_type

# --------------------------------------------------------------------------
# Boundary models
# --------------------------------------------------------------------------

class PositionRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instrument: str
    name: str | None = None
    qty: Decimal | None = None
    price: Decimal | None = None
    cost_basis: Decimal | None = None
    market_value: Decimal | None = None
    as_of: date | None = None


class TransactionRow(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instrument: str | None = None
    name: str | None = None
    trade_date: date
    settle_date: date | None = None
    raw_type: str
    canonical_type: str | None = None
    qty: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    fees: Decimal | None = None


class ImportResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    file_hash: str
    filename: str
    file_type: str
    broker_fingerprint: str
    template_name: str | None
    positions: list[PositionRow] = []
    transactions: list[TransactionRow] = []
    queued_types: list[str] = []
    column_map: dict[str, str | None] = {}
    proposals: list[MappingProposal] = []
    row_count: int = 0


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_CLEAN_NUMBER_RE = re.compile(r"[$,%\s]")
_TICKER_CLEAN_RE = re.compile(r"\s*\*+\s*$")


def _clean_decimal(raw: str) -> Decimal | None:
    s = _CLEAN_NUMBER_RE.sub("", raw.strip())
    if not s or s in ("-", "--", "N/A", "n/a"):
        return None
    # Handle parentheses as negative: (1.23) -> -1.23
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _clean_ticker(raw: str) -> str:
    return _TICKER_CLEAN_RE.sub("", raw.strip())


def _parse_date(raw: str) -> date | None:
    if not raw.strip():
        return None
    try:
        return parse_date(raw.strip(), dayfirst=False).date()
    except Exception:
        return None


def hash_file(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def read_csv_rows(content: bytes) -> list[list[str]]:
    """Read raw CSV bytes into a list-of-lists (all strings)."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return list(reader)


# --------------------------------------------------------------------------
# Parse rows into typed boundary models
# --------------------------------------------------------------------------

def _parse_position_row(
    mapped: dict[str, str],
    template: MappingTemplate | None = None,
) -> PositionRow | None:
    instrument = _clean_ticker(mapped.get("symbol", mapped.get("name", "")))
    if not instrument:
        return None
    return PositionRow(
        instrument=instrument,
        name=mapped.get("name") or None,
        qty=_clean_decimal(mapped.get("qty", "")),
        price=_clean_decimal(mapped.get("price", "")),
        cost_basis=_clean_decimal(mapped.get("cost_basis", "")),
        market_value=_clean_decimal(mapped.get("market_value", "")),
        as_of=_parse_date(mapped.get("as_of", "")),
    )


def _parse_transaction_row(
    mapped: dict[str, str],
    template: MappingTemplate | None,
    queued_types: list[str],
) -> TransactionRow | None:
    trade_date_raw = mapped.get("trade_date", "")
    trade_date = _parse_date(trade_date_raw)
    if trade_date is None:
        return None

    raw_type = mapped.get("raw_type", "").strip()
    if not raw_type:
        return None

    extra_aliases = template.type_aliases if template else {}
    canonical = canonicalize_type(raw_type, extra_aliases)
    if canonical is None and raw_type and raw_type not in queued_types:
        queued_types.append(raw_type)

    instrument_raw = mapped.get("symbol", "")
    instrument = _clean_ticker(instrument_raw) if instrument_raw else None

    return TransactionRow(
        instrument=instrument or None,
        name=mapped.get("name") or None,
        trade_date=trade_date,
        settle_date=_parse_date(mapped.get("settle_date", "")),
        raw_type=raw_type,
        canonical_type=canonical,
        qty=_clean_decimal(mapped.get("qty", "")),
        price=_clean_decimal(mapped.get("price", "")),
        amount=_clean_decimal(mapped.get("amount", "")),
        fees=_clean_decimal(mapped.get("fees", "")),
    )


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------

def process_file(
    content: bytes,
    filename: str,
    column_map_override: dict[str, str | None] | None = None,
    template_override: MappingTemplate | None = None,
    profiles_dir: Path | None = None,
) -> ImportResult:
    """Run the full import pipeline on raw file bytes.

    If column_map_override is supplied (from interactive review), it is used
    directly. Otherwise we try to load a saved template by fingerprint, then
    fall back to proposals.
    """
    from pathlib import Path as _Path
    _profiles_dir = profiles_dir or _Path("profiles")

    file_hash = hash_file(content)
    raw_rows = read_csv_rows(content)
    headers, data_rows = preclean(raw_rows)

    if not headers:
        raise ValueError("Could not detect a header row in the file")

    fingerprint = compute_fingerprint(headers)
    template: MappingTemplate | None = template_override

    if template is None and column_map_override is None:
        template = load_template(fingerprint, _profiles_dir)

    proposals = propose_mapping(headers)

    if column_map_override is not None:
        column_map = column_map_override
    elif template is not None:
        column_map = template.column_map
    else:
        column_map = {p.source_col: p.canonical_field for p in proposals}

    canonical_fields = {v for v in column_map.values() if v is not None}
    file_type = detect_file_type(canonical_fields)
    if file_type is None:
        raise ValueError(
            f"Cannot determine file type from mapped columns: {canonical_fields}. "
            "Please specify with --type positions|transactions"
        )

    positions: list[PositionRow] = []
    transactions: list[TransactionRow] = []
    queued_types: list[str] = []

    for row in data_rows:
        mapped = apply_column_map(column_map, row, headers)
        if file_type == "positions":
            parsed = _parse_position_row(mapped, template)
            if parsed is not None:
                positions.append(parsed)
        else:
            parsed_t = _parse_transaction_row(mapped, template, queued_types)
            if parsed_t is not None:
                transactions.append(parsed_t)

    row_count = len(positions) if file_type == "positions" else len(transactions)

    return ImportResult(
        file_hash=file_hash,
        filename=filename,
        file_type=file_type,
        broker_fingerprint=fingerprint,
        template_name=template.name if template else None,
        positions=positions,
        transactions=transactions,
        queued_types=queued_types,
        column_map=column_map,
        proposals=proposals,
        row_count=row_count,
    )
