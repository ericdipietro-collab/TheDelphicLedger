"""Broker-compatible CSV export for TradeProposal objects.

Supports Fidelity Batch Trade CSV and Schwab Order Import CSV.
Only imports from committee.core.types, stdlib csv, io, and decimal.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from committee.core.types import TradeProposal

MissingBrokerFieldWarning = str

_FIDELITY_FIELDNAMES = [
    "Account Number",
    "Symbol",
    "Action",
    "Quantity",
    "Order Type",
    "Duration",
    "Memo",
]

_SCHWAB_FIELDNAMES = [
    "Account",
    "Symbol",
    "Action",
    "Quantity",
    "Order Type",
    "Duration",
    "Description",
]


def _build_memo(oracle_id: str, tags: list[str]) -> str:
    parts = [f"oracle:{oracle_id}"] + tags
    return "|".join(parts)


def _whole_qty(qty: Decimal) -> str:
    return str(qty.quantize(Decimal("1")))


def export_fidelity_csv(
    proposals: list[TradeProposal],
    ticker_map: dict[int, str],
    oracle_id: str,
    account_map: dict[str, str],
) -> tuple[str, list[MissingBrokerFieldWarning]]:
    """Fidelity Batch Trade CSV. Skips CASH tickers. Warns (does not fail) for missing account mappings."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_FIDELITY_FIELDNAMES,
        lineterminator="\r\n",
    )
    writer.writeheader()

    warnings: list[MissingBrokerFieldWarning] = []

    for p in proposals:
        ticker = ticker_map.get(p.instrument_id, "")
        if ticker == "CASH":
            continue

        broker_account = account_map.get(p.account_id)
        if broker_account is None:
            warnings.append(
                f"No broker account mapping for account_key={p.account_id!r}; using account_key as fallback"
            )
            broker_account = p.account_id

        action = "Buy" if p.direction == "buy" else "Sell"
        memo = _build_memo(oracle_id, p.rationale_tags)

        writer.writerow({
            "Account Number": broker_account,
            "Symbol": ticker,
            "Action": action,
            "Quantity": _whole_qty(p.qty),
            "Order Type": "Market",
            "Duration": "Day",
            "Memo": memo,
        })

    return buf.getvalue(), warnings


def export_schwab_csv(
    proposals: list[TradeProposal],
    ticker_map: dict[int, str],
    oracle_id: str,
    account_map: dict[str, str],
) -> tuple[str, list[MissingBrokerFieldWarning]]:
    """Schwab Order Import CSV."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=_SCHWAB_FIELDNAMES,
        lineterminator="\r\n",
    )
    writer.writeheader()

    warnings: list[MissingBrokerFieldWarning] = []

    for p in proposals:
        ticker = ticker_map.get(p.instrument_id, "")
        if ticker == "CASH":
            continue

        broker_account = account_map.get(p.account_id)
        if broker_account is None:
            warnings.append(
                f"No broker account mapping for account_key={p.account_id!r}; using account_key as fallback"
            )
            broker_account = p.account_id

        action = "Buy to Open" if p.direction == "buy" else "Sell to Close"
        description = _build_memo(oracle_id, p.rationale_tags)

        writer.writerow({
            "Account": broker_account,
            "Symbol": ticker,
            "Action": action,
            "Quantity": _whole_qty(p.qty),
            "Order Type": "Market",
            "Duration": "Good for Day",
            "Description": description,
        })

    return buf.getvalue(), warnings
