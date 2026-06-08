"""Static alias table: known sweep/cash tickers → canonical cash instrument key."""

from __future__ import annotations

from sqlalchemy.orm import Session

from committee.models import Instrument

# Canonical ticker for the generic cash/uninvested position.
CASH_TICKER = "$CASH"

# Sweep tickers that map to the cash instrument.
# Keys are upper-cased raw ticker strings.
SWEEP_ALIASES: dict[str, str] = {
    # Generic
    "CASH": CASH_TICKER,
    "USD": CASH_TICKER,
    "FDIC": CASH_TICKER,
    "PENDING": CASH_TICKER,
    "PENDING ACTIVITY": CASH_TICKER,
    # Fidelity core positions / sweeps
    "FDRXX": CASH_TICKER,
    "FCASH": CASH_TICKER,
    "FZFXX": CASH_TICKER,
    "SPAXX": CASH_TICKER,
    "SPRXX": CASH_TICKER,
    # Vanguard core
    "VMFXX": CASH_TICKER,
    "VMMXX": CASH_TICKER,
    # Schwab core
    "SWVXX": CASH_TICKER,
    "SNVXX": CASH_TICKER,
}


def get_or_create_cash(session: Session) -> Instrument:
    """Return (and if needed, create) the canonical cash instrument."""
    from sqlalchemy import select

    inst = session.execute(
        select(Instrument).where(Instrument.ticker == CASH_TICKER)
    ).scalar_one_or_none()
    if inst is None:
        inst = Instrument(
            ticker=CASH_TICKER,
            name="Cash / Money-Market Sweep",
            instrument_type="cash",
            asset_class="cash",
            sleeve="cash",
            is_cash_equivalent=True,
            needs_unwind=False,
            aliases=[],
            bundle_tags=[],
        )
        session.add(inst)
        session.flush()
    return inst
