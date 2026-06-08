"""Auto-detect whether a file contains positions or transactions from mapped columns."""

from __future__ import annotations

_POSITION_SIGNALS = {"market_value", "cost_basis", "qty", "price"}
_TRANSACTION_SIGNALS = {"trade_date", "raw_type", "amount", "settle_date"}

# A single field is enough to strongly signal the type
_POSITION_STRONG = {"market_value", "cost_basis"}
_TRANSACTION_STRONG = {"trade_date", "raw_type"}


def detect_file_type(canonical_fields: set[str]) -> str | None:
    """Return 'positions', 'transactions', or None if ambiguous."""
    if canonical_fields & _TRANSACTION_STRONG:
        return "transactions"
    if canonical_fields & _POSITION_STRONG:
        return "positions"
    txn_score = len(canonical_fields & _TRANSACTION_SIGNALS)
    pos_score = len(canonical_fields & _POSITION_SIGNALS)
    if txn_score > pos_score:
        return "transactions"
    if pos_score > txn_score:
        return "positions"
    return None
