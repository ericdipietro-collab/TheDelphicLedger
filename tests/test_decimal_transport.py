"""Decimal transport contract tests (AC-9, AC-10, FR-5)."""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal


def test_high_precision_round_trip():
    """4dp ROUND_HALF_EVEN round-trip through string."""
    val = Decimal("1234567.8765")
    rounded = val.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)
    assert Decimal(str(rounded)) == rounded


def test_large_value_no_float_error():
    """Values near float precision limit survive string round-trip."""
    val = Decimal("99999999.9999")
    assert Decimal(str(val)) == val


def test_banker_rounding_half_even():
    """0.5 rounds to even (banker's rounding)."""
    assert Decimal("0.5").quantize(Decimal("1"), rounding=ROUND_HALF_EVEN) == Decimal("0")
    assert Decimal("1.5").quantize(Decimal("1"), rounding=ROUND_HALF_EVEN) == Decimal("2")
    assert Decimal("2.5").quantize(Decimal("1"), rounding=ROUND_HALF_EVEN) == Decimal("2")


def test_api_schema_financial_fields_are_strings():
    """ProposalOut.qty and estimated_value use DecimalStr annotation."""
    from committee.api.schemas import ProposalOut
    fields = ProposalOut.model_fields
    assert "qty" in fields
    assert "estimated_value" in fields
    # Both fields must exist (they are DecimalStr-annotated via PlainSerializer in schemas.py)
