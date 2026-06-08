from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class DecimalText(TypeDecorator[Decimal]):
    """Store Decimal as TEXT in SQLite to avoid float contamination."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return str(Decimal(value))

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)
