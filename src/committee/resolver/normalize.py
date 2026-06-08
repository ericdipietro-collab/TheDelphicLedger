"""Ticker normalization: BRK.B ≡ BRK/B ≡ BRK B → same canonical form."""

from __future__ import annotations

import re

_PUNCT = re.compile(r"[\.\-/\s]+")


def normalize_ticker(raw: str) -> str:
    """Strip punctuation/spaces and uppercase.

    BRK.B → BRKB, BRK/B → BRKB, BRK B → BRKB.
    """
    return _PUNCT.sub("", raw.strip()).upper()
