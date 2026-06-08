"""PriceAdapter protocol and PriceObs dataclass."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Protocol, runtime_checkable


@dataclass
class PriceObs:
    observed_date: date
    adj_close: Decimal
    dividend: Decimal = field(default_factory=lambda: Decimal("0"))


@runtime_checkable
class PriceAdapter(Protocol):
    source_name: str

    def fetch_eod(self, ticker: str, start: date, end: date) -> list[PriceObs]:
        ...
