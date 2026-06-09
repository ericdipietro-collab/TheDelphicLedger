"""Data refresh endpoints: prices, macro (FRED), holdings rebuild, and sleeve classification."""

from __future__ import annotations

import threading
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.models import BundleState, Holding, Instrument, MarketObservation, UniverseEntry

router = APIRouter(prefix="/api/data", tags=["data"])
SessionDep = Annotated[Session, Depends(get_session)]

_progress_lock = threading.Lock()
_fetch_progress: dict[str, object] = {"operation": None, "current": 0, "total": 0}


class RefreshResult(BaseModel):
    ok: bool
    message: str
    count: int = 0


# ── Setup status ──────────────────────────────────────────────────────────────

class SetupStatus(BaseModel):
    has_holdings: bool
    holding_count: int
    has_prices: bool
    price_count: int
    has_macro: bool
    has_edgar: bool
    bundles_seeded: bool
    bundles_enabled: int
    universe_size: int
    has_run: bool
    unresolved_count: int          # NEW
    prices_as_of: date | None      # NEW
    edgar_as_of: date | None       # NEW


@router.get("/setup-status", response_model=SetupStatus)
def setup_status(session: SessionDep) -> SetupStatus:
    """Return a snapshot of which setup steps have been completed."""
    from committee.models import Fundamental, UnresolvedQueue

    holding_count: int = session.execute(
        select(func.count()).select_from(Holding).where(Holding.market_value.isnot(None))
    ).scalar_one() or 0

    price_count: int = session.execute(
        select(func.count()).select_from(MarketObservation)
        .where(MarketObservation.unit == "USD_adj_close")
    ).scalar_one() or 0

    has_macro: bool = (session.execute(
        select(func.count()).select_from(MarketObservation)
        .where(MarketObservation.source == "fred")
    ).scalar_one() or 0) > 0

    has_edgar: bool = (session.execute(
        select(func.count()).select_from(Fundamental)
    ).scalar_one() or 0) > 0

    bundle_state_count: int = session.execute(
        select(func.count()).select_from(BundleState)
    ).scalar_one() or 0

    bundles_enabled: int = session.execute(
        select(func.count()).select_from(BundleState)
        .where(BundleState.enabled == True)  # noqa: E712
    ).scalar_one() or 0

    universe_size: int = session.execute(
        select(func.count()).select_from(UniverseEntry)
    ).scalar_one() or 0

    from committee.models import Decision
    has_run = (session.execute(
        select(func.count()).select_from(Decision)
    ).scalar_one() or 0) > 0

    unresolved_count: int = session.execute(
        select(func.count()).select_from(UnresolvedQueue)
        .where(UnresolvedQueue.queue_type == "instrument")
        .where(UnresolvedQueue.resolved_at.is_(None))
    ).scalar_one() or 0

    prices_as_of: date | None = session.execute(
        select(func.max(MarketObservation.observed_date))
        .where(MarketObservation.unit == "USD_adj_close")
    ).scalar_one()

    edgar_as_of: date | None = session.execute(
        select(func.max(Fundamental.filed_at))
    ).scalar_one()

    return SetupStatus(
        has_holdings=holding_count > 0,
        holding_count=holding_count,
        has_prices=price_count > 0,
        price_count=price_count,
        has_macro=has_macro,
        has_edgar=has_edgar,
        bundles_seeded=bundle_state_count > 0,
        bundles_enabled=bundles_enabled,
        universe_size=universe_size,
        has_run=has_run,
        unresolved_count=unresolved_count,
        prices_as_of=prices_as_of,
        edgar_as_of=edgar_as_of,
    )


class FetchProgress(BaseModel):
    operation: str | None
    current: int
    total: int


@router.get("/fetch-progress", response_model=FetchProgress)
def fetch_progress_endpoint() -> FetchProgress:
    """Return current fetch operation progress. Polls every second from the UI."""
    with _progress_lock:
        return FetchProgress(
            operation=_fetch_progress["operation"],  # type: ignore[arg-type]
            current=int(_fetch_progress["current"]),
            total=int(_fetch_progress["total"]),
        )


# ── Fetch prices ──────────────────────────────────────────────────────────────

@router.post("/fetch-prices", response_model=RefreshResult)
def fetch_prices(session: SessionDep) -> RefreshResult:
    """Fetch 380-day EOD prices for all holdings + SPY benchmark."""
    from committee.market.persist import save_price_observations
    from committee.market.prices import get_price_adapter

    instruments: list[Instrument] = session.execute(
        select(Instrument)
        .join(Holding, Holding.instrument_id == Instrument.id)
        .distinct()
    ).scalars().all()

    if not instruments:
        return RefreshResult(ok=False, message="No holdings to fetch prices for.", count=0)

    # Ensure SPY exists as a benchmark instrument (needed for beta_vs_spy metric)
    spy_inst = session.execute(select(Instrument).where(Instrument.ticker == "SPY")).scalar_one_or_none()
    if spy_inst is None:
        spy_inst = Instrument(
            ticker="SPY", name="SPDR S&P 500 ETF Trust",
            instrument_type="etf", asset_class="equity", sleeve="unclassified",
            is_cash_equivalent=False, needs_unwind=False, aliases=[], bundle_tags=[],
        )
        session.add(spy_inst)
        session.flush()

    # Also include universe instruments so their prices stay fresh for the buy loop
    universe_insts: list[Instrument] = session.execute(
        select(Instrument).join(UniverseEntry, UniverseEntry.instrument_id == Instrument.id)
    ).scalars().all()

    # Combine holdings + universe + SPY, deduplicated
    all_insts: list[Instrument] = list({i.id: i for i in [*instruments, *universe_insts, spy_inst]}.values())

    adapter = get_price_adapter()
    end = date.today()
    # 380 calendar days ≈ 263 trading days — enough for 12-1 momentum (253 needed) and 200-day MA
    start = end - timedelta(days=380)

    # Pre-compute most-recent observation date per instrument in one query.
    # Skip instruments already fresh within 3 calendar days (covers weekends).
    freshness_cutoff = end - timedelta(days=3)
    all_iids = [i.id for i in all_insts]
    fresh_iids: set[int] = set(
        row[0]
        for row in session.execute(
            select(MarketObservation.instrument_id, func.max(MarketObservation.observed_date))
            .where(
                MarketObservation.instrument_id.in_(all_iids),
                MarketObservation.unit == "USD_adj_close",
            )
            .group_by(MarketObservation.instrument_id)
            .having(func.max(MarketObservation.observed_date) >= freshness_cutoff)
        ).all()
    )

    to_fetch = [i for i in all_insts if i.ticker and i.id not in fresh_iids]

    with _progress_lock:
        _fetch_progress.update({"operation": "prices", "current": 0, "total": len(to_fetch)})

    total = 0
    skipped = 0
    fetched_n = 0
    errors: list[str] = []
    unavailable: list[str] = []
    for inst in all_insts:
        if not inst.ticker:
            continue
        if inst.id in fresh_iids:
            skipped += 1
            continue
        fetched_n += 1
        with _progress_lock:
            _fetch_progress["current"] = fetched_n
        try:
            obs = adapter.fetch_eod(inst.ticker, start, end)
            n = save_price_observations(
                session=session,
                source=adapter.source_name,
                ticker=inst.ticker,
                instrument_id=inst.id,
                obs_list=[(o.observed_date, o.adj_close, o.dividend) for o in obs],
            )
            total += n
        except Exception as e:
            msg_lower = str(e).lower()
            # 404 / "not found" / "no data" = price unavailable for this ticker, not a system error
            if any(k in msg_lower for k in ("404", "not found", "no data", "delisted", "no timezone")):
                unavailable.append(inst.ticker)
            else:
                errors.append(f"{inst.ticker}: {e}")

    with _progress_lock:
        _fetch_progress.update({"operation": None, "current": 0, "total": 0})

    session.commit()

    fetched_count = len(all_insts) - skipped
    parts: list[str] = [f"Fetched {total} new observations for {fetched_count} instruments; {skipped} already fresh (skipped)."]
    if unavailable:
        parts.append(f"No price data: {', '.join(unavailable)} (OTC/preferred/delisted — normal).")
    if errors:
        parts.append(f"Errors: {'; '.join(errors[:3])}")

    return RefreshResult(ok=not errors, message=" ".join(parts), count=total)


# ── Fetch macro (FRED) ────────────────────────────────────────────────────────

@router.post("/fetch-macro", response_model=RefreshResult)
def fetch_macro(session: SessionDep) -> RefreshResult:
    """Fetch all configured FRED macro series."""
    from committee.market.fred import FRED_SERIES, fetch_all_fred
    from committee.market.persist import save_fred_observations

    try:
        all_series = fetch_all_fred()
    except Exception as e:
        return RefreshResult(ok=False, message=f"FRED fetch failed: {e}", count=0)

    total = 0
    for series_id, obs_list in all_series.items():
        unit = FRED_SERIES.get(series_id, "derived")
        n = save_fred_observations(
            session=session,
            series_id=series_id,
            obs_list=[(o.observed_date, o.value, unit) for o in obs_list],
        )
        total += n

    session.commit()

    return RefreshResult(
        ok=True,
        message=f"Fetched {total} new macro observations across {len(all_series)} series.",
        count=total,
    )


# ── Fetch EDGAR fundamentals ──────────────────────────────────────────────────

@router.post("/fetch-edgar", response_model=RefreshResult)
def fetch_edgar(session: SessionDep) -> RefreshResult:
    """Fetch annual XBRL fundamentals from SEC EDGAR for all equity holdings."""
    from committee.market.edgar import fetch_all_edgar
    from committee.market.persist import save_fundamentals

    instruments: list[Instrument] = session.execute(
        select(Instrument)
        .join(Holding, Holding.instrument_id == Instrument.id)
        .where(Instrument.instrument_type == "stock")
        .distinct()
    ).scalars().all()

    if not instruments:
        return RefreshResult(ok=False, message="No stock holdings to fetch fundamentals for.", count=0)

    pairs = [(i.ticker, i.id) for i in instruments if i.ticker]
    results = fetch_all_edgar(pairs)

    total = 0
    errors: list[str] = []
    unavailable: list[str] = []

    for result in results:
        if result.error:
            if result.error == "no_cik":
                unavailable.append(result.ticker)
            else:
                msg_lower = result.error.lower()
                if any(k in msg_lower for k in ("404", "not found", "no data")):
                    unavailable.append(result.ticker)
                else:
                    errors.append(f"{result.ticker}: {result.error}")
            continue
        n = save_fundamentals(
            session=session,
            instrument_id=result.instrument_id,
            obs_list=[
                (o.period_end, o.filed_at, o.metric, o.value, o.unit)
                for o in result.obs
            ],
        )
        total += n

    session.commit()

    parts: list[str] = [f"Fetched {total} new fundamental observations for {len(pairs)} instruments."]
    if unavailable:
        parts.append(f"No EDGAR data: {', '.join(unavailable)} (ETF/foreign/OTC — normal).")
    if errors:
        parts.append(f"Errors: {'; '.join(errors[:3])}")

    return RefreshResult(ok=not errors, message=" ".join(parts), count=total)


# ── Rebuild holdings ──────────────────────────────────────────────────────────

@router.post("/rebuild-holdings", response_model=RefreshResult)
def rebuild_holdings_endpoint(session: SessionDep) -> RefreshResult:
    """Re-derive holdings from latest position snapshots."""
    from committee.holdings import rebuild_holdings

    rows = rebuild_holdings(session)
    session.commit()

    resolved = sum(1 for r in rows if r.is_resolved)
    unresolved = len(rows) - resolved
    msg = f"Rebuilt {resolved} holdings."
    if unresolved:
        msg += f" {unresolved} positions still unresolved — run committee resolve."

    return RefreshResult(ok=True, message=msg, count=resolved)


# ── Classify sleeves ──────────────────────────────────────────────────────────

# Known-ticker overrides for instruments that import as "unclassified".
# Tuple: (instrument_type, asset_class, sleeve)
_TICKER_OVERRIDES: dict[str, tuple[str, str, str]] = {
    # US large-cap stocks commonly imported without metadata
    "AAPL": ("stock", "equity", "equity_us"),
    "AMZN": ("stock", "equity", "equity_us"),
    "BLK":  ("stock", "equity", "equity_us"),
    "CVX":  ("stock", "equity", "equity_us"),
    "GOOGL":("stock", "equity", "equity_us"),
    "GOOG": ("stock", "equity", "equity_us"),
    "META": ("stock", "equity", "equity_us"),
    "MSFT": ("stock", "equity", "equity_us"),
    "NVDA": ("stock", "equity", "equity_us"),
    "SPG":  ("stock", "equity", "equity_us"),   # REIT, equity_us
    "TSLA": ("stock", "equity", "equity_us"),
    "VZ":   ("stock", "equity", "equity_us"),
    "XOM":  ("stock", "equity", "equity_us"),
    # Gold / commodity ETFs
    "GLD":  ("etf", "alternatives", "alternatives"),
    "IAU":  ("etf", "alternatives", "alternatives"),
    "SLV":  ("etf", "alternatives", "alternatives"),
    "USO":  ("etf", "alternatives", "alternatives"),
    # US broad equity ETFs
    "IWM":  ("etf", "equity", "equity_us"),
    "QQQ":  ("etf", "equity", "equity_us"),
    "SCHB": ("etf", "equity", "equity_us"),
    "SCHM": ("etf", "equity", "equity_us"),
    "SPY":  ("etf", "equity", "equity_us"),
    "VTI":  ("etf", "equity", "equity_us"),
    # International equity ETFs
    "EFA":  ("etf", "equity", "equity_intl"),
    "IEFA": ("etf", "equity", "equity_intl"),
    "VEA":  ("etf", "equity", "equity_intl"),
    # Fixed income ETFs
    "AGG":  ("etf", "fixed_income", "fixed_income"),
    "BND":  ("etf", "fixed_income", "fixed_income"),
    "SCHI": ("etf", "fixed_income", "fixed_income"),
    "SGOV": ("etf", "fixed_income", "fixed_income"),
    "VGIT": ("etf", "fixed_income", "fixed_income"),
    "TLT":  ("etf", "fixed_income", "fixed_income"),
    # International stocks — exchange-listed ADRs and direct listings
    "ASML": ("stock", "equity", "equity_intl"),  # Netherlands
    "NVS":  ("stock", "equity", "equity_intl"),  # Switzerland
    "ABB":  ("stock", "equity", "equity_intl"),  # Switzerland
    "AZN":  ("stock", "equity", "equity_intl"),  # UK/Sweden
    "GSK":  ("stock", "equity", "equity_intl"),  # UK
    "UL":   ("stock", "equity", "equity_intl"),  # UK/Netherlands
    "BTI":  ("stock", "equity", "equity_intl"),  # UK
    "DEO":  ("stock", "equity", "equity_intl"),  # UK
    "BP":   ("stock", "equity", "equity_intl"),  # UK
    "SHEL": ("stock", "equity", "equity_intl"),  # UK/Netherlands
    "BCS":  ("stock", "equity", "equity_intl"),  # UK
    "HSBC": ("stock", "equity", "equity_intl"),  # UK/HK
    "SAP":  ("stock", "equity", "equity_intl"),  # Germany
    "ING":  ("stock", "equity", "equity_intl"),  # Netherlands
    "SAN":  ("stock", "equity", "equity_intl"),  # Spain
    "TTE":  ("stock", "equity", "equity_intl"),  # France
    "E":    ("stock", "equity", "equity_intl"),  # Italy
    "TSM":  ("stock", "equity", "equity_intl"),  # Taiwan
    "TM":   ("stock", "equity", "equity_intl"),  # Japan
    "SONY": ("stock", "equity", "equity_intl"),  # Japan
    "HMC":  ("stock", "equity", "equity_intl"),  # Japan
    "MFG":  ("stock", "equity", "equity_intl"),  # Japan
    "BHP":  ("stock", "equity", "equity_intl"),  # Australia
    "RIO":  ("stock", "equity", "equity_intl"),  # Australia/UK
    "TD":   ("stock", "equity", "equity_intl"),  # Canada
    "RY":   ("stock", "equity", "equity_intl"),  # Canada
    "ENB":  ("stock", "equity", "equity_intl"),  # Canada
    "BAM":  ("stock", "equity", "equity_intl"),  # Canada
    "CNI":  ("stock", "equity", "equity_intl"),  # Canada
    "CP":   ("stock", "equity", "equity_intl"),  # Canada
    "HDB":  ("stock", "equity", "equity_intl"),  # India
    "IBN":  ("stock", "equity", "equity_intl"),  # India
    "INFY": ("stock", "equity", "equity_intl"),  # India
    "WIT":  ("stock", "equity", "equity_intl"),  # India
    # International stocks — OTC F-suffix (also caught by heuristic)
    "BPZZF":("stock", "equity", "equity_intl"),  # Canada
}

# OTC foreign tickers end in F (e.g. BPZZF, NESOF, RYDAF)
_FOREIGN_OTC_SUFFIXES = ("F",)

# asset_class → sleeve fallback (when no override and no ticker heuristic matches)
_ASSET_CLASS_TO_SLEEVE: dict[str, str] = {
    "equity":       "equity_us",   # default assumption for US brokerage account
    "fixed_income": "fixed_income",
    "alternatives": "alternatives",
    "cash":         "cash",
}


def _infer_sleeve(ticker: str, itype: str | None, asset_class: str | None) -> tuple[str, str, str] | None:
    """Return (instrument_type, asset_class, sleeve) or None if can't determine."""
    t = ticker.upper()

    # Check hardcoded override first
    if t in _TICKER_OVERRIDES:
        return _TICKER_OVERRIDES[t]

    # Must have a known asset_class to continue
    if not asset_class or asset_class == "unclassified":
        return None

    # OTC foreign heuristic: ticker ends with F and is longer than 4 chars
    if len(t) >= 5 and t.endswith(_FOREIGN_OTC_SUFFIXES) and asset_class == "equity":
        return (itype or "stock", asset_class, "equity_intl")

    sleeve = _ASSET_CLASS_TO_SLEEVE.get(asset_class)
    if sleeve is None:
        return None
    return (itype or "stock", asset_class, sleeve)


@router.post("/classify-sleeves", response_model=RefreshResult)
def classify_sleeves(session: SessionDep) -> RefreshResult:
    """Assign sleeve (and fix instrument_type/asset_class) for unclassified instruments.

    Only updates instruments that currently have sleeve='unclassified' or sleeve=NULL.
    Safe to run repeatedly — already-classified instruments are skipped.
    """
    instruments: list[Instrument] = session.execute(
        select(Instrument).where(
            (Instrument.sleeve == "unclassified") | Instrument.sleeve.is_(None)
        )
    ).scalars().all()

    updated = 0
    skipped: list[str] = []

    for inst in instruments:
        inferred = _infer_sleeve(inst.ticker or "", inst.instrument_type, inst.asset_class)
        if inferred is None:
            skipped.append(inst.ticker or "?")
            continue
        new_type, new_class, new_sleeve = inferred
        if inst.instrument_type in (None, "unclassified"):
            inst.instrument_type = new_type
        if inst.asset_class in (None, "unclassified"):
            inst.asset_class = new_class
        inst.sleeve = new_sleeve
        updated += 1

    session.commit()

    parts = [f"Classified {updated} instruments."]
    if skipped:
        parts.append(f"Still unclassified (add to override map): {', '.join(skipped[:10])}")
    return RefreshResult(ok=True, message=" ".join(parts), count=updated)


# ── Bundle system ──────────────────────────────────────────────────────────────

# Each bundle is: bundle_id → { display_name, instruments: [(ticker, type, class, sleeve, name)] }
# Instruments may appear in multiple bundles; bundle_tags accumulates all IDs.
_BUNDLES: dict[str, dict] = {
    "etf_core": {
        "display_name": "ETF Core (~80 diversified ETFs)",
        "instruments": [
            # Equity US — broad market
            ("VTI",  "etf", "equity",       "equity_us",    "Vanguard Total Stock Market ETF"),
            ("VOO",  "etf", "equity",       "equity_us",    "Vanguard S&P 500 ETF"),
            ("IVV",  "etf", "equity",       "equity_us",    "iShares Core S&P 500 ETF"),
            ("QQQ",  "etf", "equity",       "equity_us",    "Invesco QQQ Trust"),
            ("IWM",  "etf", "equity",       "equity_us",    "iShares Russell 2000 ETF"),
            ("SCHB", "etf", "equity",       "equity_us",    "Schwab U.S. Broad Market ETF"),
            ("SPY",  "etf", "equity",       "equity_us",    "SPDR S&P 500 ETF Trust"),
            ("IJR",  "etf", "equity",       "equity_us",    "iShares Core S&P Small-Cap ETF"),
            ("MDY",  "etf", "equity",       "equity_us",    "SPDR S&P MidCap 400 ETF"),
            ("VUG",  "etf", "equity",       "equity_us",    "Vanguard Growth ETF"),
            ("VTV",  "etf", "equity",       "equity_us",    "Vanguard Value ETF"),
            ("SCHG", "etf", "equity",       "equity_us",    "Schwab U.S. Large-Cap Growth ETF"),
            ("SCHV", "etf", "equity",       "equity_us",    "Schwab U.S. Large-Cap Value ETF"),
            ("VNQ",  "etf", "equity",       "equity_us",    "Vanguard Real Estate ETF"),
            ("VO",   "etf", "equity",       "equity_us",    "Vanguard Mid-Cap ETF"),
            ("VB",   "etf", "equity",       "equity_us",    "Vanguard Small-Cap ETF"),
            ("MGK",  "etf", "equity",       "equity_us",    "Vanguard Mega Cap Growth ETF"),
            ("SCHA", "etf", "equity",       "equity_us",    "Schwab U.S. Small-Cap ETF"),
            ("SCHM", "etf", "equity",       "equity_us",    "Schwab U.S. Mid-Cap ETF"),
            ("IWB",  "etf", "equity",       "equity_us",    "iShares Russell 1000 ETF"),
            ("IWF",  "etf", "equity",       "equity_us",    "iShares Russell 1000 Growth ETF"),
            ("IWD",  "etf", "equity",       "equity_us",    "iShares Russell 1000 Value ETF"),
            # Equity US — sector (SPDR)
            ("XLK",  "etf", "equity",       "equity_us",    "Technology Select Sector SPDR ETF"),
            ("XLF",  "etf", "equity",       "equity_us",    "Financial Select Sector SPDR ETF"),
            ("XLV",  "etf", "equity",       "equity_us",    "Health Care Select Sector SPDR ETF"),
            ("XLE",  "etf", "equity",       "equity_us",    "Energy Select Sector SPDR ETF"),
            ("XLI",  "etf", "equity",       "equity_us",    "Industrial Select Sector SPDR ETF"),
            ("XLP",  "etf", "equity",       "equity_us",    "Consumer Staples Select Sector SPDR ETF"),
            ("XLY",  "etf", "equity",       "equity_us",    "Consumer Discretionary Select Sector SPDR ETF"),
            ("XLU",  "etf", "equity",       "equity_us",    "Utilities Select Sector SPDR ETF"),
            ("XLC",  "etf", "equity",       "equity_us",    "Communication Services Select Sector SPDR ETF"),
            # Equity US — factor
            ("MTUM", "etf", "equity",       "equity_us",    "iShares MSCI USA Momentum Factor ETF"),
            ("QUAL", "etf", "equity",       "equity_us",    "iShares MSCI USA Quality Factor ETF"),
            ("USMV", "etf", "equity",       "equity_us",    "iShares MSCI USA Min Vol Factor ETF"),
            ("SIZE", "etf", "equity",       "equity_us",    "iShares MSCI USA Size Factor ETF"),
            # Equity US — sector (Vanguard)
            ("VHT",  "etf", "equity",       "equity_us",    "Vanguard Health Care ETF"),
            ("VDE",  "etf", "equity",       "equity_us",    "Vanguard Energy ETF"),
            # Equity Intl
            ("VEA",  "etf", "equity",       "equity_intl",  "Vanguard FTSE Developed Markets ETF"),
            ("VXUS", "etf", "equity",       "equity_intl",  "Vanguard Total International Stock ETF"),
            ("EFA",  "etf", "equity",       "equity_intl",  "iShares MSCI EAFE ETF"),
            ("IEFA", "etf", "equity",       "equity_intl",  "iShares Core MSCI EAFE ETF"),
            ("VWO",  "etf", "equity",       "equity_intl",  "Vanguard FTSE Emerging Markets ETF"),
            ("EEM",  "etf", "equity",       "equity_intl",  "iShares MSCI Emerging Markets ETF"),
            ("SCHF", "etf", "equity",       "equity_intl",  "Schwab International Equity ETF"),
            ("IEMG", "etf", "equity",       "equity_intl",  "iShares Core MSCI Emerging Markets ETF"),
            ("VGK",  "etf", "equity",       "equity_intl",  "Vanguard FTSE Europe ETF"),
            ("EWJ",  "etf", "equity",       "equity_intl",  "iShares MSCI Japan ETF"),
            ("ACWI", "etf", "equity",       "equity_intl",  "iShares MSCI ACWI ETF"),
            ("EWG",  "etf", "equity",       "equity_intl",  "iShares MSCI Germany ETF"),
            ("EWZ",  "etf", "equity",       "equity_intl",  "iShares MSCI Brazil ETF"),
            ("INDA", "etf", "equity",       "equity_intl",  "iShares MSCI India ETF"),
            ("MCHI", "etf", "equity",       "equity_intl",  "iShares MSCI China ETF"),
            # Fixed income
            ("BND",  "etf", "fixed_income", "fixed_income", "Vanguard Total Bond Market ETF"),
            ("AGG",  "etf", "fixed_income", "fixed_income", "iShares Core U.S. Aggregate Bond ETF"),
            ("TLT",  "etf", "fixed_income", "fixed_income", "iShares 20+ Year Treasury Bond ETF"),
            ("VGIT", "etf", "fixed_income", "fixed_income", "Vanguard Intermediate-Term Treasury ETF"),
            ("SGOV", "etf", "fixed_income", "fixed_income", "iShares 0-3 Month Treasury Bond ETF"),
            ("SHY",  "etf", "fixed_income", "fixed_income", "iShares 1-3 Year Treasury Bond ETF"),
            ("IEF",  "etf", "fixed_income", "fixed_income", "iShares 7-10 Year Treasury Bond ETF"),
            ("LQD",  "etf", "fixed_income", "fixed_income", "iShares iBoxx IG Corporate Bond ETF"),
            ("HYG",  "etf", "fixed_income", "fixed_income", "iShares iBoxx HY Corporate Bond ETF"),
            ("VCSH", "etf", "fixed_income", "fixed_income", "Vanguard Short-Term Corporate Bond ETF"),
            ("VCIT", "etf", "fixed_income", "fixed_income", "Vanguard Intermediate-Term Corporate Bond ETF"),
            ("BNDX", "etf", "fixed_income", "fixed_income", "Vanguard Total International Bond ETF"),
            ("MUB",  "etf", "fixed_income", "fixed_income", "iShares National Muni Bond ETF"),
            ("VTIP", "etf", "fixed_income", "fixed_income", "Vanguard Short-Term Inflation-Protected ETF"),
            ("SCHZ", "etf", "fixed_income", "fixed_income", "Schwab U.S. Aggregate Bond ETF"),
            ("SCHI", "etf", "fixed_income", "fixed_income", "Schwab 5-10 Year Corporate Bond ETF"),
            ("VGSH", "etf", "fixed_income", "fixed_income", "Vanguard Short-Term Treasury ETF"),
            ("VGLT", "etf", "fixed_income", "fixed_income", "Vanguard Long-Term Treasury ETF"),
            ("TIPS", "etf", "fixed_income", "fixed_income", "iShares TIPS Bond ETF"),
            ("BIL",  "etf", "fixed_income", "fixed_income", "SPDR Bloomberg 1-3 Month T-Bill ETF"),
            ("FLOT", "etf", "fixed_income", "fixed_income", "iShares Floating Rate Bond ETF"),
            ("JNK",  "etf", "fixed_income", "fixed_income", "SPDR Bloomberg High Yield Bond ETF"),
            ("USHY", "etf", "fixed_income", "fixed_income", "iShares Broad USD High Yield Corporate Bond ETF"),
            # Alternatives
            ("IAU",  "etf", "alternatives", "alternatives", "iShares Gold Trust"),
            ("GLD",  "etf", "alternatives", "alternatives", "SPDR Gold Shares"),
            ("SLV",  "etf", "alternatives", "alternatives", "iShares Silver Trust"),
            ("GDX",  "etf", "alternatives", "alternatives", "VanEck Gold Miners ETF"),
            ("PDBC", "etf", "alternatives", "alternatives", "Invesco Optimum Yield Diversified Commodity ETF"),
        ],
    },
    "dow30": {
        "display_name": "Dow 30 (DJIA components)",
        "instruments": [
            ("AAPL", "stock", "equity", "equity_us", "Apple Inc."),
            ("AMGN", "stock", "equity", "equity_us", "Amgen Inc."),
            ("AXP",  "stock", "equity", "equity_us", "American Express Co."),
            ("BA",   "stock", "equity", "equity_us", "Boeing Co."),
            ("CAT",  "stock", "equity", "equity_us", "Caterpillar Inc."),
            ("CRM",  "stock", "equity", "equity_us", "Salesforce Inc."),
            ("CSCO", "stock", "equity", "equity_us", "Cisco Systems Inc."),
            ("CVX",  "stock", "equity", "equity_us", "Chevron Corp."),
            ("DIS",  "stock", "equity", "equity_us", "Walt Disney Co."),
            ("DOW",  "stock", "equity", "equity_us", "Dow Inc."),
            ("GS",   "stock", "equity", "equity_us", "Goldman Sachs Group Inc."),
            ("HD",   "stock", "equity", "equity_us", "Home Depot Inc."),
            ("HON",  "stock", "equity", "equity_us", "Honeywell International Inc."),
            ("IBM",  "stock", "equity", "equity_us", "International Business Machines Corp."),
            ("INTC", "stock", "equity", "equity_us", "Intel Corp."),
            ("JNJ",  "stock", "equity", "equity_us", "Johnson & Johnson"),
            ("JPM",  "stock", "equity", "equity_us", "JPMorgan Chase & Co."),
            ("KO",   "stock", "equity", "equity_us", "Coca-Cola Co."),
            ("MCD",  "stock", "equity", "equity_us", "McDonald's Corp."),
            ("MMM",  "stock", "equity", "equity_us", "3M Co."),
            ("MRK",  "stock", "equity", "equity_us", "Merck & Co. Inc."),
            ("MSFT", "stock", "equity", "equity_us", "Microsoft Corp."),
            ("NKE",  "stock", "equity", "equity_us", "Nike Inc."),
            ("PG",   "stock", "equity", "equity_us", "Procter & Gamble Co."),
            ("SHW",  "stock", "equity", "equity_us", "Sherwin-Williams Co."),
            ("TRV",  "stock", "equity", "equity_us", "Travelers Companies Inc."),
            ("UNH",  "stock", "equity", "equity_us", "UnitedHealth Group Inc."),
            ("V",    "stock", "equity", "equity_us", "Visa Inc."),
            ("VZ",   "stock", "equity", "equity_us", "Verizon Communications Inc."),
            ("WMT",  "stock", "equity", "equity_us", "Walmart Inc."),
        ],
    },
    "nasdaq_top50": {
        "display_name": "Nasdaq Top 50 (largest Nasdaq-listed stocks)",
        "instruments": [
            ("MSFT",  "stock", "equity", "equity_us", "Microsoft Corp."),
            ("AAPL",  "stock", "equity", "equity_us", "Apple Inc."),
            ("NVDA",  "stock", "equity", "equity_us", "NVIDIA Corp."),
            ("AMZN",  "stock", "equity", "equity_us", "Amazon.com Inc."),
            ("META",  "stock", "equity", "equity_us", "Meta Platforms Inc."),
            ("GOOGL", "stock", "equity", "equity_us", "Alphabet Inc. Class A"),
            ("GOOG",  "stock", "equity", "equity_us", "Alphabet Inc. Class C"),
            ("TSLA",  "stock", "equity", "equity_us", "Tesla Inc."),
            ("AVGO",  "stock", "equity", "equity_us", "Broadcom Inc."),
            ("COST",  "stock", "equity", "equity_us", "Costco Wholesale Corp."),
            ("NFLX",  "stock", "equity", "equity_us", "Netflix Inc."),
            ("AMD",   "stock", "equity", "equity_us", "Advanced Micro Devices Inc."),
            ("ADBE",  "stock", "equity", "equity_us", "Adobe Inc."),
            ("QCOM",  "stock", "equity", "equity_us", "QUALCOMM Inc."),
            ("CSCO",  "stock", "equity", "equity_us", "Cisco Systems Inc."),
            ("INTU",  "stock", "equity", "equity_us", "Intuit Inc."),
            ("AMAT",  "stock", "equity", "equity_us", "Applied Materials Inc."),
            ("TXN",   "stock", "equity", "equity_us", "Texas Instruments Inc."),
            ("HON",   "stock", "equity", "equity_us", "Honeywell International Inc."),
            ("AMGN",  "stock", "equity", "equity_us", "Amgen Inc."),
            ("SBUX",  "stock", "equity", "equity_us", "Starbucks Corp."),
            ("VRTX",  "stock", "equity", "equity_us", "Vertex Pharmaceuticals Inc."),
            ("ISRG",  "stock", "equity", "equity_us", "Intuitive Surgical Inc."),
            ("GILD",  "stock", "equity", "equity_us", "Gilead Sciences Inc."),
            ("REGN",  "stock", "equity", "equity_us", "Regeneron Pharmaceuticals Inc."),
            ("ADI",   "stock", "equity", "equity_us", "Analog Devices Inc."),
            ("LRCX",  "stock", "equity", "equity_us", "Lam Research Corp."),
            ("MU",    "stock", "equity", "equity_us", "Micron Technology Inc."),
            ("KLAC",  "stock", "equity", "equity_us", "KLA Corp."),
            ("SNPS",  "stock", "equity", "equity_us", "Synopsys Inc."),
            ("CDNS",  "stock", "equity", "equity_us", "Cadence Design Systems Inc."),
            ("MRVL",  "stock", "equity", "equity_us", "Marvell Technology Inc."),
            ("NXPI",  "stock", "equity", "equity_us", "NXP Semiconductors NV"),
            ("PANW",  "stock", "equity", "equity_us", "Palo Alto Networks Inc."),
            ("FTNT",  "stock", "equity", "equity_us", "Fortinet Inc."),
            ("PYPL",  "stock", "equity", "equity_us", "PayPal Holdings Inc."),
            ("ORLY",  "stock", "equity", "equity_us", "O'Reilly Automotive Inc."),
            ("ODFL",  "stock", "equity", "equity_us", "Old Dominion Freight Line Inc."),
            ("MNST",  "stock", "equity", "equity_us", "Monster Beverage Corp."),
            ("MELI",  "stock", "equity", "equity_us", "MercadoLibre Inc."),
            ("WDAY",  "stock", "equity", "equity_us", "Workday Inc."),
            ("CRWD",  "stock", "equity", "equity_us", "CrowdStrike Holdings Inc."),
            ("DXCM",  "stock", "equity", "equity_us", "DexCom Inc."),
            ("BIIB",  "stock", "equity", "equity_us", "Biogen Inc."),
            ("IDXX",  "stock", "equity", "equity_us", "IDEXX Laboratories Inc."),
            ("TEAM",  "stock", "equity", "equity_us", "Atlassian Corp."),
            ("ILMN",  "stock", "equity", "equity_us", "Illumina Inc."),
            ("ZS",    "stock", "equity", "equity_us", "Zscaler Inc."),
            ("MRNA",  "stock", "equity", "equity_us", "Moderna Inc."),
            ("PCAR",  "stock", "equity", "equity_us", "PACCAR Inc."),
        ],
    },
    "sp500": {
        "display_name": "S&P 500 (static snapshot of index components)",
        "instruments": [
            # Technology
            ("AAPL",  "stock", "equity", "equity_us", "Apple Inc."),
            ("MSFT",  "stock", "equity", "equity_us", "Microsoft Corp."),
            ("NVDA",  "stock", "equity", "equity_us", "NVIDIA Corp."),
            ("AVGO",  "stock", "equity", "equity_us", "Broadcom Inc."),
            ("ORCL",  "stock", "equity", "equity_us", "Oracle Corp."),
            ("ADBE",  "stock", "equity", "equity_us", "Adobe Inc."),
            ("CRM",   "stock", "equity", "equity_us", "Salesforce Inc."),
            ("AMD",   "stock", "equity", "equity_us", "Advanced Micro Devices Inc."),
            ("QCOM",  "stock", "equity", "equity_us", "QUALCOMM Inc."),
            ("INTC",  "stock", "equity", "equity_us", "Intel Corp."),
            ("TXN",   "stock", "equity", "equity_us", "Texas Instruments Inc."),
            ("AMAT",  "stock", "equity", "equity_us", "Applied Materials Inc."),
            ("INTU",  "stock", "equity", "equity_us", "Intuit Inc."),
            ("MU",    "stock", "equity", "equity_us", "Micron Technology Inc."),
            ("KLAC",  "stock", "equity", "equity_us", "KLA Corp."),
            ("LRCX",  "stock", "equity", "equity_us", "Lam Research Corp."),
            ("SNPS",  "stock", "equity", "equity_us", "Synopsys Inc."),
            ("CDNS",  "stock", "equity", "equity_us", "Cadence Design Systems Inc."),
            ("ADI",   "stock", "equity", "equity_us", "Analog Devices Inc."),
            ("NXPI",  "stock", "equity", "equity_us", "NXP Semiconductors NV"),
            ("MRVL",  "stock", "equity", "equity_us", "Marvell Technology Inc."),
            ("ON",    "stock", "equity", "equity_us", "ON Semiconductor Corp."),
            ("ANSS",  "stock", "equity", "equity_us", "ANSYS Inc."),
            ("KEYS",  "stock", "equity", "equity_us", "Keysight Technologies Inc."),
            ("TER",   "stock", "equity", "equity_us", "Teradyne Inc."),
            ("MPWR",  "stock", "equity", "equity_us", "Monolithic Power Systems Inc."),
            ("ENTG",  "stock", "equity", "equity_us", "Entegris Inc."),
            ("SWKS",  "stock", "equity", "equity_us", "Skyworks Solutions Inc."),
            ("FFIV",  "stock", "equity", "equity_us", "F5 Inc."),
            ("JNPR",  "stock", "equity", "equity_us", "Juniper Networks Inc."),
            ("VRT",   "stock", "equity", "equity_us", "Vertiv Holdings Co."),
            ("GEN",   "stock", "equity", "equity_us", "Gen Digital Inc."),
            ("IBM",   "stock", "equity", "equity_us", "International Business Machines Corp."),
            ("CSCO",  "stock", "equity", "equity_us", "Cisco Systems Inc."),
            ("NTAP",  "stock", "equity", "equity_us", "NetApp Inc."),
            ("HPQ",   "stock", "equity", "equity_us", "HP Inc."),
            ("HPE",   "stock", "equity", "equity_us", "Hewlett Packard Enterprise Co."),
            ("WDC",   "stock", "equity", "equity_us", "Western Digital Corp."),
            ("STX",   "stock", "equity", "equity_us", "Seagate Technology Holdings PLC"),
            ("ANET",  "stock", "equity", "equity_us", "Arista Networks Inc."),
            ("ZBRA",  "stock", "equity", "equity_us", "Zebra Technologies Corp."),
            ("CTSH",  "stock", "equity", "equity_us", "Cognizant Technology Solutions Corp."),
            ("ACN",   "stock", "equity", "equity_us", "Accenture PLC"),
            ("IT",    "stock", "equity", "equity_us", "Gartner Inc."),
            ("CDW",   "stock", "equity", "equity_us", "CDW Corp."),
            ("AKAM",  "stock", "equity", "equity_us", "Akamai Technologies Inc."),
            ("DT",    "stock", "equity", "equity_us", "Dynatrace Inc."),
            ("FTNT",  "stock", "equity", "equity_us", "Fortinet Inc."),
            ("PANW",  "stock", "equity", "equity_us", "Palo Alto Networks Inc."),
            ("ZS",    "stock", "equity", "equity_us", "Zscaler Inc."),
            ("CRWD",  "stock", "equity", "equity_us", "CrowdStrike Holdings Inc."),
            ("WDAY",  "stock", "equity", "equity_us", "Workday Inc."),
            ("NOW",   "stock", "equity", "equity_us", "ServiceNow Inc."),
            ("DDOG",  "stock", "equity", "equity_us", "Datadog Inc."),
            ("TEAM",  "stock", "equity", "equity_us", "Atlassian Corp."),
            # Communication Services
            ("META",  "stock", "equity", "equity_us", "Meta Platforms Inc."),
            ("GOOGL", "stock", "equity", "equity_us", "Alphabet Inc. Class A"),
            ("GOOG",  "stock", "equity", "equity_us", "Alphabet Inc. Class C"),
            ("T",     "stock", "equity", "equity_us", "AT&T Inc."),
            ("VZ",    "stock", "equity", "equity_us", "Verizon Communications Inc."),
            ("DIS",   "stock", "equity", "equity_us", "Walt Disney Co."),
            ("NFLX",  "stock", "equity", "equity_us", "Netflix Inc."),
            ("CMCSA", "stock", "equity", "equity_us", "Comcast Corp."),
            ("CHTR",  "stock", "equity", "equity_us", "Charter Communications Inc."),
            ("TMUS",  "stock", "equity", "equity_us", "T-Mobile US Inc."),
            ("OMC",   "stock", "equity", "equity_us", "Omnicom Group Inc."),
            ("IPG",   "stock", "equity", "equity_us", "Interpublic Group of Companies Inc."),
            ("TTWO",  "stock", "equity", "equity_us", "Take-Two Interactive Software Inc."),
            ("EA",    "stock", "equity", "equity_us", "Electronic Arts Inc."),
            ("LYV",   "stock", "equity", "equity_us", "Live Nation Entertainment Inc."),
            ("FOXA",  "stock", "equity", "equity_us", "Fox Corp. Class A"),
            ("WBD",   "stock", "equity", "equity_us", "Warner Bros. Discovery Inc."),
            ("PARA",  "stock", "equity", "equity_us", "Paramount Global Class B"),
            # Consumer Discretionary
            ("AMZN",  "stock", "equity", "equity_us", "Amazon.com Inc."),
            ("TSLA",  "stock", "equity", "equity_us", "Tesla Inc."),
            ("HD",    "stock", "equity", "equity_us", "Home Depot Inc."),
            ("MCD",   "stock", "equity", "equity_us", "McDonald's Corp."),
            ("NKE",   "stock", "equity", "equity_us", "Nike Inc."),
            ("SBUX",  "stock", "equity", "equity_us", "Starbucks Corp."),
            ("LOW",   "stock", "equity", "equity_us", "Lowe's Companies Inc."),
            ("TGT",   "stock", "equity", "equity_us", "Target Corp."),
            ("BKNG",  "stock", "equity", "equity_us", "Booking Holdings Inc."),
            ("ORLY",  "stock", "equity", "equity_us", "O'Reilly Automotive Inc."),
            ("AZO",   "stock", "equity", "equity_us", "AutoZone Inc."),
            ("ROST",  "stock", "equity", "equity_us", "Ross Stores Inc."),
            ("DLTR",  "stock", "equity", "equity_us", "Dollar Tree Inc."),
            ("BBY",   "stock", "equity", "equity_us", "Best Buy Co. Inc."),
            ("DG",    "stock", "equity", "equity_us", "Dollar General Corp."),
            ("YUM",   "stock", "equity", "equity_us", "Yum! Brands Inc."),
            ("CMG",   "stock", "equity", "equity_us", "Chipotle Mexican Grill Inc."),
            ("DPZ",   "stock", "equity", "equity_us", "Domino's Pizza Inc."),
            ("APTV",  "stock", "equity", "equity_us", "Aptiv PLC"),
            ("MGM",   "stock", "equity", "equity_us", "MGM Resorts International"),
            ("LVS",   "stock", "equity", "equity_us", "Las Vegas Sands Corp."),
            ("WYNN",  "stock", "equity", "equity_us", "Wynn Resorts Ltd."),
            ("RCL",   "stock", "equity", "equity_us", "Royal Caribbean Cruises Ltd."),
            ("CCL",   "stock", "equity", "equity_us", "Carnival Corp."),
            ("NCLH",  "stock", "equity", "equity_us", "Norwegian Cruise Line Holdings Ltd."),
            ("DAL",   "stock", "equity", "equity_us", "Delta Air Lines Inc."),
            ("UAL",   "stock", "equity", "equity_us", "United Airlines Holdings Inc."),
            ("AAL",   "stock", "equity", "equity_us", "American Airlines Group Inc."),
            ("LUV",   "stock", "equity", "equity_us", "Southwest Airlines Co."),
            ("EXPE",  "stock", "equity", "equity_us", "Expedia Group Inc."),
            ("PHM",   "stock", "equity", "equity_us", "PulteGroup Inc."),
            ("DHI",   "stock", "equity", "equity_us", "D.R. Horton Inc."),
            ("LEN",   "stock", "equity", "equity_us", "Lennar Corp."),
            ("NVR",   "stock", "equity", "equity_us", "NVR Inc."),
            ("TOL",   "stock", "equity", "equity_us", "Toll Brothers Inc."),
            ("GRMN",  "stock", "equity", "equity_us", "Garmin Ltd."),
            ("HAS",   "stock", "equity", "equity_us", "Hasbro Inc."),
            ("MAT",   "stock", "equity", "equity_us", "Mattel Inc."),
            ("F",     "stock", "equity", "equity_us", "Ford Motor Co."),
            ("GM",    "stock", "equity", "equity_us", "General Motors Co."),
            # Consumer Staples
            ("WMT",   "stock", "equity", "equity_us", "Walmart Inc."),
            ("PG",    "stock", "equity", "equity_us", "Procter & Gamble Co."),
            ("KO",    "stock", "equity", "equity_us", "Coca-Cola Co."),
            ("PEP",   "stock", "equity", "equity_us", "PepsiCo Inc."),
            ("COST",  "stock", "equity", "equity_us", "Costco Wholesale Corp."),
            ("PM",    "stock", "equity", "equity_us", "Philip Morris International Inc."),
            ("MO",    "stock", "equity", "equity_us", "Altria Group Inc."),
            ("MDLZ",  "stock", "equity", "equity_us", "Mondelez International Inc."),
            ("CL",    "stock", "equity", "equity_us", "Colgate-Palmolive Co."),
            ("KMB",   "stock", "equity", "equity_us", "Kimberly-Clark Corp."),
            ("HSY",   "stock", "equity", "equity_us", "Hershey Co."),
            ("GIS",   "stock", "equity", "equity_us", "General Mills Inc."),
            ("K",     "stock", "equity", "equity_us", "Kellanova"),
            ("CPB",   "stock", "equity", "equity_us", "Campbell Soup Co."),
            ("MKC",   "stock", "equity", "equity_us", "McCormick & Co. Inc."),
            ("SJM",   "stock", "equity", "equity_us", "J.M. Smucker Co."),
            ("CAG",   "stock", "equity", "equity_us", "Conagra Brands Inc."),
            ("HRL",   "stock", "equity", "equity_us", "Hormel Foods Corp."),
            ("TSN",   "stock", "equity", "equity_us", "Tyson Foods Inc."),
            ("KHC",   "stock", "equity", "equity_us", "Kraft Heinz Co."),
            ("MNST",  "stock", "equity", "equity_us", "Monster Beverage Corp."),
            ("EL",    "stock", "equity", "equity_us", "Estee Lauder Companies Inc."),
            ("CHD",   "stock", "equity", "equity_us", "Church & Dwight Co. Inc."),
            ("CLX",   "stock", "equity", "equity_us", "Clorox Co."),
            ("KR",    "stock", "equity", "equity_us", "Kroger Co."),
            # Energy
            ("XOM",   "stock", "equity", "equity_us", "Exxon Mobil Corp."),
            ("CVX",   "stock", "equity", "equity_us", "Chevron Corp."),
            ("COP",   "stock", "equity", "equity_us", "ConocoPhillips"),
            ("EOG",   "stock", "equity", "equity_us", "EOG Resources Inc."),
            ("SLB",   "stock", "equity", "equity_us", "SLB"),
            ("OXY",   "stock", "equity", "equity_us", "Occidental Petroleum Corp."),
            ("MPC",   "stock", "equity", "equity_us", "Marathon Petroleum Corp."),
            ("PSX",   "stock", "equity", "equity_us", "Phillips 66"),
            ("VLO",   "stock", "equity", "equity_us", "Valero Energy Corp."),
            ("HES",   "stock", "equity", "equity_us", "Hess Corp."),
            ("DVN",   "stock", "equity", "equity_us", "Devon Energy Corp."),
            ("HAL",   "stock", "equity", "equity_us", "Halliburton Co."),
            ("BKR",   "stock", "equity", "equity_us", "Baker Hughes Co."),
            ("FANG",  "stock", "equity", "equity_us", "Diamondback Energy Inc."),
            ("CTRA",  "stock", "equity", "equity_us", "Coterra Energy Inc."),
            ("APA",   "stock", "equity", "equity_us", "APA Corp."),
            ("MRO",   "stock", "equity", "equity_us", "Marathon Oil Corp."),
            ("EQT",   "stock", "equity", "equity_us", "EQT Corp."),
            # Financials
            ("JPM",   "stock", "equity", "equity_us", "JPMorgan Chase & Co."),
            ("BAC",   "stock", "equity", "equity_us", "Bank of America Corp."),
            ("WFC",   "stock", "equity", "equity_us", "Wells Fargo & Co."),
            ("GS",    "stock", "equity", "equity_us", "Goldman Sachs Group Inc."),
            ("MS",    "stock", "equity", "equity_us", "Morgan Stanley"),
            ("C",     "stock", "equity", "equity_us", "Citigroup Inc."),
            ("BLK",   "stock", "equity", "equity_us", "BlackRock Inc."),
            ("SPGI",  "stock", "equity", "equity_us", "S&P Global Inc."),
            ("MCO",   "stock", "equity", "equity_us", "Moody's Corp."),
            ("ICE",   "stock", "equity", "equity_us", "Intercontinental Exchange Inc."),
            ("CME",   "stock", "equity", "equity_us", "CME Group Inc."),
            ("CBOE",  "stock", "equity", "equity_us", "Cboe Global Markets Inc."),
            ("NDAQ",  "stock", "equity", "equity_us", "Nasdaq Inc."),
            ("MSCI",  "stock", "equity", "equity_us", "MSCI Inc."),
            ("CB",    "stock", "equity", "equity_us", "Chubb Ltd."),
            ("PGR",   "stock", "equity", "equity_us", "Progressive Corp."),
            ("MET",   "stock", "equity", "equity_us", "MetLife Inc."),
            ("PRU",   "stock", "equity", "equity_us", "Prudential Financial Inc."),
            ("AFL",   "stock", "equity", "equity_us", "Aflac Inc."),
            ("ALL",   "stock", "equity", "equity_us", "Allstate Corp."),
            ("TRV",   "stock", "equity", "equity_us", "Travelers Companies Inc."),
            ("AIG",   "stock", "equity", "equity_us", "American International Group Inc."),
            ("HIG",   "stock", "equity", "equity_us", "Hartford Financial Services Group Inc."),
            ("BK",    "stock", "equity", "equity_us", "Bank of New York Mellon Corp."),
            ("STT",   "stock", "equity", "equity_us", "State Street Corp."),
            ("SCHW",  "stock", "equity", "equity_us", "Charles Schwab Corp."),
            ("AXP",   "stock", "equity", "equity_us", "American Express Co."),
            ("V",     "stock", "equity", "equity_us", "Visa Inc."),
            ("MA",    "stock", "equity", "equity_us", "Mastercard Inc."),
            ("PYPL",  "stock", "equity", "equity_us", "PayPal Holdings Inc."),
            ("COF",   "stock", "equity", "equity_us", "Capital One Financial Corp."),
            ("DFS",   "stock", "equity", "equity_us", "Discover Financial Services"),
            ("SYF",   "stock", "equity", "equity_us", "Synchrony Financial"),
            ("USB",   "stock", "equity", "equity_us", "U.S. Bancorp"),
            ("TFC",   "stock", "equity", "equity_us", "Truist Financial Corp."),
            ("MTB",   "stock", "equity", "equity_us", "M&T Bank Corp."),
            ("KEY",   "stock", "equity", "equity_us", "KeyCorp"),
            ("RF",    "stock", "equity", "equity_us", "Regions Financial Corp."),
            ("HBAN",  "stock", "equity", "equity_us", "Huntington Bancshares Inc."),
            ("CFG",   "stock", "equity", "equity_us", "Citizens Financial Group Inc."),
            ("FITB",  "stock", "equity", "equity_us", "Fifth Third Bancorp"),
            ("AMP",   "stock", "equity", "equity_us", "Ameriprise Financial Inc."),
            ("TROW",  "stock", "equity", "equity_us", "T. Rowe Price Group Inc."),
            ("IVZ",   "stock", "equity", "equity_us", "Invesco Ltd."),
            ("BEN",   "stock", "equity", "equity_us", "Franklin Resources Inc."),
            ("NTRS",  "stock", "equity", "equity_us", "Northern Trust Corp."),
            ("RJF",   "stock", "equity", "equity_us", "Raymond James Financial Inc."),
            ("WRB",   "stock", "equity", "equity_us", "W. R. Berkley Corp."),
            ("CINF",  "stock", "equity", "equity_us", "Cincinnati Financial Corp."),
            # Healthcare
            ("UNH",   "stock", "equity", "equity_us", "UnitedHealth Group Inc."),
            ("LLY",   "stock", "equity", "equity_us", "Eli Lilly and Co."),
            ("JNJ",   "stock", "equity", "equity_us", "Johnson & Johnson"),
            ("ABBV",  "stock", "equity", "equity_us", "AbbVie Inc."),
            ("MRK",   "stock", "equity", "equity_us", "Merck & Co. Inc."),
            ("TMO",   "stock", "equity", "equity_us", "Thermo Fisher Scientific Inc."),
            ("ABT",   "stock", "equity", "equity_us", "Abbott Laboratories"),
            ("AMGN",  "stock", "equity", "equity_us", "Amgen Inc."),
            ("PFE",   "stock", "equity", "equity_us", "Pfizer Inc."),
            ("CVS",   "stock", "equity", "equity_us", "CVS Health Corp."),
            ("CI",    "stock", "equity", "equity_us", "The Cigna Group"),
            ("BMY",   "stock", "equity", "equity_us", "Bristol-Myers Squibb Co."),
            ("MDT",   "stock", "equity", "equity_us", "Medtronic PLC"),
            ("ELV",   "stock", "equity", "equity_us", "Elevance Health Inc."),
            ("HUM",   "stock", "equity", "equity_us", "Humana Inc."),
            ("ZTS",   "stock", "equity", "equity_us", "Zoetis Inc."),
            ("GILD",  "stock", "equity", "equity_us", "Gilead Sciences Inc."),
            ("REGN",  "stock", "equity", "equity_us", "Regeneron Pharmaceuticals Inc."),
            ("VRTX",  "stock", "equity", "equity_us", "Vertex Pharmaceuticals Inc."),
            ("ISRG",  "stock", "equity", "equity_us", "Intuitive Surgical Inc."),
            ("DXCM",  "stock", "equity", "equity_us", "DexCom Inc."),
            ("BIIB",  "stock", "equity", "equity_us", "Biogen Inc."),
            ("IDXX",  "stock", "equity", "equity_us", "IDEXX Laboratories Inc."),
            ("IQV",   "stock", "equity", "equity_us", "IQVIA Holdings Inc."),
            ("BDX",   "stock", "equity", "equity_us", "Becton Dickinson and Co."),
            ("BSX",   "stock", "equity", "equity_us", "Boston Scientific Corp."),
            ("SYK",   "stock", "equity", "equity_us", "Stryker Corp."),
            ("BAX",   "stock", "equity", "equity_us", "Baxter International Inc."),
            ("EW",    "stock", "equity", "equity_us", "Edwards Lifesciences Corp."),
            ("HOLX",  "stock", "equity", "equity_us", "Hologic Inc."),
            ("ALGN",  "stock", "equity", "equity_us", "Align Technology Inc."),
            ("PODD",  "stock", "equity", "equity_us", "Insulet Corp."),
            ("RMD",   "stock", "equity", "equity_us", "ResMed Inc."),
            ("INCY",  "stock", "equity", "equity_us", "Incyte Corp."),
            ("MRNA",  "stock", "equity", "equity_us", "Moderna Inc."),
            ("MOH",   "stock", "equity", "equity_us", "Molina Healthcare Inc."),
            ("CNC",   "stock", "equity", "equity_us", "Centene Corp."),
            ("HCA",   "stock", "equity", "equity_us", "HCA Healthcare Inc."),
            ("UHS",   "stock", "equity", "equity_us", "Universal Health Services Inc."),
            ("A",     "stock", "equity", "equity_us", "Agilent Technologies Inc."),
            ("ILMN",  "stock", "equity", "equity_us", "Illumina Inc."),
            ("MTD",   "stock", "equity", "equity_us", "Mettler-Toledo International Inc."),
            ("WAT",   "stock", "equity", "equity_us", "Waters Corp."),
            ("GEHC",  "stock", "equity", "equity_us", "GE HealthCare Technologies Inc."),
            ("ZBH",   "stock", "equity", "equity_us", "Zimmer Biomet Holdings Inc."),
            ("RGEN",  "stock", "equity", "equity_us", "Repligen Corp."),
            ("VTRS",  "stock", "equity", "equity_us", "Viatris Inc."),
            ("OGN",   "stock", "equity", "equity_us", "Organon & Co."),
            # Industrials
            ("RTX",   "stock", "equity", "equity_us", "RTX Corp."),
            ("HON",   "stock", "equity", "equity_us", "Honeywell International Inc."),
            ("BA",    "stock", "equity", "equity_us", "Boeing Co."),
            ("CAT",   "stock", "equity", "equity_us", "Caterpillar Inc."),
            ("LMT",   "stock", "equity", "equity_us", "Lockheed Martin Corp."),
            ("GE",    "stock", "equity", "equity_us", "GE Aerospace"),
            ("NOC",   "stock", "equity", "equity_us", "Northrop Grumman Corp."),
            ("GD",    "stock", "equity", "equity_us", "General Dynamics Corp."),
            ("UPS",   "stock", "equity", "equity_us", "United Parcel Service Inc."),
            ("FDX",   "stock", "equity", "equity_us", "FedEx Corp."),
            ("CSX",   "stock", "equity", "equity_us", "CSX Corp."),
            ("NSC",   "stock", "equity", "equity_us", "Norfolk Southern Corp."),
            ("UNP",   "stock", "equity", "equity_us", "Union Pacific Corp."),
            ("EMR",   "stock", "equity", "equity_us", "Emerson Electric Co."),
            ("ETN",   "stock", "equity", "equity_us", "Eaton Corp. PLC"),
            ("PH",    "stock", "equity", "equity_us", "Parker-Hannifin Corp."),
            ("ROK",   "stock", "equity", "equity_us", "Rockwell Automation Inc."),
            ("IEX",   "stock", "equity", "equity_us", "IDEX Corp."),
            ("GNRC",  "stock", "equity", "equity_us", "Generac Holdings Inc."),
            ("HUBB",  "stock", "equity", "equity_us", "Hubbell Inc."),
            ("FAST",  "stock", "equity", "equity_us", "Fastenal Co."),
            ("GWW",   "stock", "equity", "equity_us", "W.W. Grainger Inc."),
            ("URI",   "stock", "equity", "equity_us", "United Rentals Inc."),
            ("AME",   "stock", "equity", "equity_us", "AMETEK Inc."),
            ("CARR",  "stock", "equity", "equity_us", "Carrier Global Corp."),
            ("OTIS",  "stock", "equity", "equity_us", "Otis Worldwide Corp."),
            ("TT",    "stock", "equity", "equity_us", "Trane Technologies PLC"),
            ("JCI",   "stock", "equity", "equity_us", "Johnson Controls International PLC"),
            ("SWK",   "stock", "equity", "equity_us", "Stanley Black & Decker Inc."),
            ("SNA",   "stock", "equity", "equity_us", "Snap-on Inc."),
            ("PNR",   "stock", "equity", "equity_us", "Pentair PLC"),
            ("CMI",   "stock", "equity", "equity_us", "Cummins Inc."),
            ("CTAS",  "stock", "equity", "equity_us", "Cintas Corp."),
            ("VRSK",  "stock", "equity", "equity_us", "Verisk Analytics Inc."),
            ("CPRT",  "stock", "equity", "equity_us", "Copart Inc."),
            ("EFX",   "stock", "equity", "equity_us", "Equifax Inc."),
            ("EXPD",  "stock", "equity", "equity_us", "Expeditors International of Washington Inc."),
            ("JBHT",  "stock", "equity", "equity_us", "J.B. Hunt Transport Services Inc."),
            ("FTV",   "stock", "equity", "equity_us", "Fortive Corp."),
            ("LDOS",  "stock", "equity", "equity_us", "Leidos Holdings Inc."),
            ("SAIC",  "stock", "equity", "equity_us", "Science Applications International Corp."),
            ("BAH",   "stock", "equity", "equity_us", "Booz Allen Hamilton Holding Corp."),
            ("L",     "stock", "equity", "equity_us", "Loews Corp."),
            ("MMM",   "stock", "equity", "equity_us", "3M Co."),
            ("DOV",   "stock", "equity", "equity_us", "Dover Corp."),
            ("XYL",   "stock", "equity", "equity_us", "Xylem Inc."),
            ("MAS",   "stock", "equity", "equity_us", "Masco Corp."),
            ("IR",    "stock", "equity", "equity_us", "Ingersoll Rand Inc."),
            ("WAB",   "stock", "equity", "equity_us", "Westinghouse Air Brake Technologies Corp."),
            ("LHX",   "stock", "equity", "equity_us", "L3Harris Technologies Inc."),
            ("TDG",   "stock", "equity", "equity_us", "TransDigm Group Inc."),
            ("HWM",   "stock", "equity", "equity_us", "Howmet Aerospace Inc."),
            ("CW",    "stock", "equity", "equity_us", "Curtiss-Wright Corp."),
            # Materials
            ("LIN",   "stock", "equity", "equity_us", "Linde PLC"),
            ("APD",   "stock", "equity", "equity_us", "Air Products and Chemicals Inc."),
            ("PPG",   "stock", "equity", "equity_us", "PPG Industries Inc."),
            ("SHW",   "stock", "equity", "equity_us", "Sherwin-Williams Co."),
            ("ECL",   "stock", "equity", "equity_us", "Ecolab Inc."),
            ("DD",    "stock", "equity", "equity_us", "DuPont de Nemours Inc."),
            ("DOW",   "stock", "equity", "equity_us", "Dow Inc."),
            ("LYB",   "stock", "equity", "equity_us", "LyondellBasell Industries NV"),
            ("CE",    "stock", "equity", "equity_us", "Celanese Corp."),
            ("NEM",   "stock", "equity", "equity_us", "Newmont Corp."),
            ("FCX",   "stock", "equity", "equity_us", "Freeport-McMoRan Inc."),
            ("ALB",   "stock", "equity", "equity_us", "Albemarle Corp."),
            ("EMN",   "stock", "equity", "equity_us", "Eastman Chemical Co."),
            ("AVY",   "stock", "equity", "equity_us", "Avery Dennison Corp."),
            ("AMCR",  "stock", "equity", "equity_us", "Amcor PLC"),
            ("PKG",   "stock", "equity", "equity_us", "Packaging Corp. of America"),
            ("IP",    "stock", "equity", "equity_us", "International Paper Co."),
            ("SEE",   "stock", "equity", "equity_us", "Sealed Air Corp."),
            ("BALL",  "stock", "equity", "equity_us", "Ball Corp."),
            ("CCK",   "stock", "equity", "equity_us", "Crown Holdings Inc."),
            ("CF",    "stock", "equity", "equity_us", "CF Industries Holdings Inc."),
            ("MOS",   "stock", "equity", "equity_us", "Mosaic Co."),
            ("FMC",   "stock", "equity", "equity_us", "FMC Corp."),
            # Real Estate
            ("PLD",   "stock", "equity", "equity_us", "Prologis Inc."),
            ("AMT",   "stock", "equity", "equity_us", "American Tower Corp."),
            ("CCI",   "stock", "equity", "equity_us", "Crown Castle Inc."),
            ("EQIX",  "stock", "equity", "equity_us", "Equinix Inc."),
            ("PSA",   "stock", "equity", "equity_us", "Public Storage"),
            ("DLR",   "stock", "equity", "equity_us", "Digital Realty Trust Inc."),
            ("SPG",   "stock", "equity", "equity_us", "Simon Property Group Inc."),
            ("O",     "stock", "equity", "equity_us", "Realty Income Corp."),
            ("WELL",  "stock", "equity", "equity_us", "Welltower Inc."),
            ("VICI",  "stock", "equity", "equity_us", "VICI Properties Inc."),
            ("EXR",   "stock", "equity", "equity_us", "Extra Space Storage Inc."),
            ("AVB",   "stock", "equity", "equity_us", "AvalonBay Communities Inc."),
            ("EQR",   "stock", "equity", "equity_us", "Equity Residential"),
            ("MAA",   "stock", "equity", "equity_us", "Mid-America Apartment Communities Inc."),
            ("UDR",   "stock", "equity", "equity_us", "UDR Inc."),
            ("ESS",   "stock", "equity", "equity_us", "Essex Property Trust Inc."),
            ("NNN",   "stock", "equity", "equity_us", "NNN REIT Inc."),
            ("WPC",   "stock", "equity", "equity_us", "W. P. Carey Inc."),
            ("BXP",   "stock", "equity", "equity_us", "BXP Inc."),
            ("KIM",   "stock", "equity", "equity_us", "Kimco Realty Corp."),
            ("REG",   "stock", "equity", "equity_us", "Regency Centers Corp."),
            ("FRT",   "stock", "equity", "equity_us", "Federal Realty Investment Trust"),
            # Utilities
            ("NEE",   "stock", "equity", "equity_us", "NextEra Energy Inc."),
            ("DUK",   "stock", "equity", "equity_us", "Duke Energy Corp."),
            ("SO",    "stock", "equity", "equity_us", "Southern Co."),
            ("D",     "stock", "equity", "equity_us", "Dominion Energy Inc."),
            ("AEP",   "stock", "equity", "equity_us", "American Electric Power Co. Inc."),
            ("EXC",   "stock", "equity", "equity_us", "Exelon Corp."),
            ("XEL",   "stock", "equity", "equity_us", "Xcel Energy Inc."),
            ("SRE",   "stock", "equity", "equity_us", "Sempra"),
            ("ES",    "stock", "equity", "equity_us", "Eversource Energy"),
            ("WEC",   "stock", "equity", "equity_us", "WEC Energy Group Inc."),
            ("DTE",   "stock", "equity", "equity_us", "DTE Energy Co."),
            ("PPL",   "stock", "equity", "equity_us", "PPL Corp."),
            ("CMS",   "stock", "equity", "equity_us", "CMS Energy Corp."),
            ("ETR",   "stock", "equity", "equity_us", "Entergy Corp."),
            ("FE",    "stock", "equity", "equity_us", "FirstEnergy Corp."),
            ("CNP",   "stock", "equity", "equity_us", "CenterPoint Energy Inc."),
            ("NI",    "stock", "equity", "equity_us", "NiSource Inc."),
            ("AEE",   "stock", "equity", "equity_us", "Ameren Corp."),
            ("LNT",   "stock", "equity", "equity_us", "Alliant Energy Corp."),
            ("AWK",   "stock", "equity", "equity_us", "American Water Works Co. Inc."),
            ("PNW",   "stock", "equity", "equity_us", "Pinnacle West Capital Corp."),
            ("EVRG",  "stock", "equity", "equity_us", "Evergy Inc."),
            # GICS additions — misc large caps not covered above
            ("BRK.B", "stock", "equity", "equity_us", "Berkshire Hathaway Inc. Class B"),
            ("UBER",  "stock", "equity", "equity_us", "Uber Technologies Inc."),
            ("LYFT",  "stock", "equity", "equity_us", "Lyft Inc."),
            ("ABNB",  "stock", "equity", "equity_us", "Airbnb Inc."),
            ("DASH",  "stock", "equity", "equity_us", "DoorDash Inc."),
            ("SHOP",  "stock", "equity", "equity_us", "Shopify Inc."),
            ("SQ",    "stock", "equity", "equity_us", "Block Inc."),
            ("SNOW",  "stock", "equity", "equity_us", "Snowflake Inc."),
            ("PLTR",  "stock", "equity", "equity_us", "Palantir Technologies Inc."),
            ("COIN",  "stock", "equity", "equity_us", "Coinbase Global Inc."),
            ("RBLX",  "stock", "equity", "equity_us", "Roblox Corp."),
            ("ZM",    "stock", "equity", "equity_us", "Zoom Video Communications Inc."),
            ("OKTA",  "stock", "equity", "equity_us", "Okta Inc."),
            ("MDB",   "stock", "equity", "equity_us", "MongoDB Inc."),
            ("NET",   "stock", "equity", "equity_us", "Cloudflare Inc."),
            ("HUBS",  "stock", "equity", "equity_us", "HubSpot Inc."),
            ("TTD",   "stock", "equity", "equity_us", "Trade Desk Inc."),
            ("TWLO",  "stock", "equity", "equity_us", "Twilio Inc."),
            ("DOCN",  "stock", "equity", "equity_us", "DigitalOcean Holdings Inc."),
        ],
    },
    "russell2000": {
        "display_name": "Russell 2000 (representative small-cap snapshot)",
        "instruments": [
            # Technology small caps
            ("QLYS",  "stock", "equity", "equity_us", "Qualys Inc."),
            ("ALRM",  "stock", "equity", "equity_us", "Alarm.com Holdings Inc."),
            ("PRGS",  "stock", "equity", "equity_us", "Progress Software Corp."),
            ("EVTC",  "stock", "equity", "equity_us", "EVERTEC Inc."),
            ("OSPN",  "stock", "equity", "equity_us", "OneSpan Inc."),
            ("PAYO",  "stock", "equity", "equity_us", "Payoneer Global Inc."),
            ("TASK",  "stock", "equity", "equity_us", "TaskUs Inc."),
            ("JAMF",  "stock", "equity", "equity_us", "Jamf Holding Corp."),
            ("BCOV",  "stock", "equity", "equity_us", "Brightcove Inc."),
            ("EGHT",  "stock", "equity", "equity_us", "8x8 Inc."),
            ("KLIC",  "stock", "equity", "equity_us", "Kulicke and Soffa Industries Inc."),
            ("FORM",  "stock", "equity", "equity_us", "FormFactor Inc."),
            ("ICHR",  "stock", "equity", "equity_us", "Ichor Holdings Ltd."),
            ("ONTO",  "stock", "equity", "equity_us", "Onto Innovation Inc."),
            ("COHU",  "stock", "equity", "equity_us", "Cohu Inc."),
            ("ACLS",  "stock", "equity", "equity_us", "Axcelis Technologies Inc."),
            ("UCTT",  "stock", "equity", "equity_us", "Ultra Clean Holdings Inc."),
            ("SMTC",  "stock", "equity", "equity_us", "Semtech Corp."),
            ("DIOD",  "stock", "equity", "equity_us", "Diodes Inc."),
            ("CEVA",  "stock", "equity", "equity_us", "CEVA Inc."),
            ("LYTS",  "stock", "equity", "equity_us", "LSI Industries Inc."),
            ("PCTY",  "stock", "equity", "equity_us", "Paylocity Holding Corp."),
            ("PAYC",  "stock", "equity", "equity_us", "Paycom Software Inc."),
            ("MGNI",  "stock", "equity", "equity_us", "Magnite Inc."),
            ("DV",    "stock", "equity", "equity_us", "DoubleVerify Holdings Inc."),
            ("APP",   "stock", "equity", "equity_us", "AppLovin Corp."),
            ("PUBM",  "stock", "equity", "equity_us", "PubMatic Inc."),
            ("RAMP",  "stock", "equity", "equity_us", "LiveRamp Holdings Inc."),
            ("RDVT",  "stock", "equity", "equity_us", "Red Violet Inc."),
            ("SMAR",  "stock", "equity", "equity_us", "Smartsheet Inc."),
            ("APPF",  "stock", "equity", "equity_us", "AppFolio Inc."),
            ("NCNO",  "stock", "equity", "equity_us", "nCino Inc."),
            ("CWAN",  "stock", "equity", "equity_us", "Clearwater Analytics Holdings Inc."),
            ("TNET",  "stock", "equity", "equity_us", "TriNet Group Inc."),
            ("HCKT",  "stock", "equity", "equity_us", "Hackett Group Inc."),
            ("EXLS",  "stock", "equity", "equity_us", "ExlService Holdings Inc."),
            ("EPAM",  "stock", "equity", "equity_us", "EPAM Systems Inc."),
            # Healthcare small caps
            ("ACAD",  "stock", "equity", "equity_us", "ACADIA Pharmaceuticals Inc."),
            ("ADMA",  "stock", "equity", "equity_us", "ADMA Biologics Inc."),
            ("AGIO",  "stock", "equity", "equity_us", "Agios Pharmaceuticals Inc."),
            ("AKRO",  "stock", "equity", "equity_us", "Akero Therapeutics Inc."),
            ("ALEC",  "stock", "equity", "equity_us", "Alector Inc."),
            ("AMPH",  "stock", "equity", "equity_us", "Amphastar Pharmaceuticals Inc."),
            ("ANGO",  "stock", "equity", "equity_us", "AngioDynamics Inc."),
            ("ARWR",  "stock", "equity", "equity_us", "Arrowhead Pharmaceuticals Inc."),
            ("ASND",  "stock", "equity", "equity_us", "Ascendis Pharma A/S"),
            ("ATRI",  "stock", "equity", "equity_us", "Atrion Corp."),
            ("BLFS",  "stock", "equity", "equity_us", "BioLife Solutions Inc."),
            ("CLDX",  "stock", "equity", "equity_us", "Celldex Therapeutics Inc."),
            ("CNMD",  "stock", "equity", "equity_us", "CONMED Corp."),
            ("CORT",  "stock", "equity", "equity_us", "Corcept Therapeutics Inc."),
            ("DAWN",  "stock", "equity", "equity_us", "Day One Biopharmaceuticals Inc."),
            ("DOCS",  "stock", "equity", "equity_us", "Doximity Inc."),
            ("DXPE",  "stock", "equity", "equity_us", "DXP Enterprises Inc."),
            ("EMED",  "stock", "equity", "equity_us", "Envision Healthcare Corp."),
            ("ENSG",  "stock", "equity", "equity_us", "Ensign Group Inc."),
            ("ENVA",  "stock", "equity", "equity_us", "Enova International Inc."),
            ("EVBG",  "stock", "equity", "equity_us", "Everbridge Inc."),
            ("FWRD",  "stock", "equity", "equity_us", "Forward Air Corp."),
            ("GDRX",  "stock", "equity", "equity_us", "GoodRx Holdings Inc."),
            ("GKOS",  "stock", "equity", "equity_us", "Glaukos Corp."),
            ("GMED",  "stock", "equity", "equity_us", "Globus Medical Inc."),
            ("HALO",  "stock", "equity", "equity_us", "Halozyme Therapeutics Inc."),
            ("ICUI",  "stock", "equity", "equity_us", "ICU Medical Inc."),
            ("INMD",  "stock", "equity", "equity_us", "InMode Ltd."),
            ("ITGR",  "stock", "equity", "equity_us", "Integer Holdings Corp."),
            ("IRTC",  "stock", "equity", "equity_us", "iRhythm Technologies Inc."),
            ("KRYS",  "stock", "equity", "equity_us", "Krystal Biotech Inc."),
            ("LGND",  "stock", "equity", "equity_us", "Ligand Pharmaceuticals Inc."),
            ("LMAT",  "stock", "equity", "equity_us", "LeMaitre Vascular Inc."),
            ("MDXG",  "stock", "equity", "equity_us", "MiMedx Group Inc."),
            ("MMSI",  "stock", "equity", "equity_us", "Merit Medical Systems Inc."),
            ("NARI",  "stock", "equity", "equity_us", "Inari Medical Inc."),
            ("NVCR",  "stock", "equity", "equity_us", "NovoCure Ltd."),
            ("NVST",  "stock", "equity", "equity_us", "Envista Holdings Corp."),
            ("OMCL",  "stock", "equity", "equity_us", "Omnicell Inc."),
            ("ONEM",  "stock", "equity", "equity_us", "1Life Healthcare Inc."),
            ("PACB",  "stock", "equity", "equity_us", "Pacific Biosciences of California Inc."),
            ("PNTG",  "stock", "equity", "equity_us", "Pennant Group Inc."),
            ("PRCT",  "stock", "equity", "equity_us", "PROCEPT BioRobotics Corp."),
            ("RVNC",  "stock", "equity", "equity_us", "Revance Therapeutics Inc."),
            ("SBSW",  "stock", "equity", "equity_us", "Sandstorm Gold Royalties"),
            ("SGMO",  "stock", "equity", "equity_us", "Sangamo Therapeutics Inc."),
            ("SLNO",  "stock", "equity", "equity_us", "Soleno Therapeutics Inc."),
            ("TMDX",  "stock", "equity", "equity_us", "TransMedics Group Inc."),
            ("TNXP",  "stock", "equity", "equity_us", "Tonix Pharmaceuticals Holding Corp."),
            ("TPVG",  "stock", "equity", "equity_us", "TriplePoint Venture Growth BDC Corp."),
            ("TTGT",  "stock", "equity", "equity_us", "TechTarget Inc."),
            ("VCNX",  "stock", "equity", "equity_us", "Vaccinex Inc."),
            # Financial small caps
            ("ABCB",  "stock", "equity", "equity_us", "Ameris Bancorp"),
            ("AMAL",  "stock", "equity", "equity_us", "Amalgamated Financial Corp."),
            ("BANF",  "stock", "equity", "equity_us", "BancFirst Corp."),
            ("BANR",  "stock", "equity", "equity_us", "Banner Financial Corp."),
            ("BRKL",  "stock", "equity", "equity_us", "Brookline Bancorp Inc."),
            ("BSVN",  "stock", "equity", "equity_us", "Bank7 Corp."),
            ("BY",    "stock", "equity", "equity_us", "Byline Bancorp Inc."),
            ("CADE",  "stock", "equity", "equity_us", "Cadence Bank"),
            ("CALB",  "stock", "equity", "equity_us", "California BanCorp"),
            ("CASH",  "stock", "equity", "equity_us", "Pathward Financial Inc."),
            ("CATC",  "stock", "equity", "equity_us", "Cambridge Bancorp"),
            ("CBSH",  "stock", "equity", "equity_us", "Commerce Bancshares Inc."),
            ("CFFN",  "stock", "equity", "equity_us", "Capitol Federal Financial Inc."),
            ("CLBK",  "stock", "equity", "equity_us", "Columbia Financial Inc."),
            ("COLB",  "stock", "equity", "equity_us", "Columbia Banking System Inc."),
            ("COOP",  "stock", "equity", "equity_us", "Mr. Cooper Group Inc."),
            ("CVBF",  "stock", "equity", "equity_us", "CVB Financial Corp."),
            ("EFC",   "stock", "equity", "equity_us", "Ellington Financial Inc."),
            ("EFSC",  "stock", "equity", "equity_us", "Enterprise Financial Services Corp."),
            ("EWBC",  "stock", "equity", "equity_us", "East West Bancorp Inc."),
            ("FAF",   "stock", "equity", "equity_us", "First American Financial Corp."),
            ("FFBC",  "stock", "equity", "equity_us", "First Financial Bancorp"),
            ("FFIN",  "stock", "equity", "equity_us", "First Financial Bankshares Inc."),
            ("FHI",   "stock", "equity", "equity_us", "Federated Hermes Inc."),
            ("FISI",  "stock", "equity", "equity_us", "Financial Institutions Inc."),
            ("FMAO",  "stock", "equity", "equity_us", "Farmers & Merchants Financial Group Inc."),
            ("FNB",   "stock", "equity", "equity_us", "FNB Corp."),
            ("FRME",  "stock", "equity", "equity_us", "First Merchants Corp."),
            ("GABC",  "stock", "equity", "equity_us", "German American Bancorp Inc."),
            ("GBCI",  "stock", "equity", "equity_us", "Glacier Bancorp Inc."),
            ("HFWA",  "stock", "equity", "equity_us", "Heritage Financial Corp."),
            ("HOPE",  "stock", "equity", "equity_us", "Hope Bancorp Inc."),
            ("HTLF",  "stock", "equity", "equity_us", "Heartland Financial USA Inc."),
            ("IBCP",  "stock", "equity", "equity_us", "Independent Bank Corp. (Michigan)"),
            ("INDB",  "stock", "equity", "equity_us", "Independent Bank Group Inc."),
            ("ISNPY", "stock", "equity", "equity_us", "Intesa Sanpaolo ADR"),
            ("ISBC",  "stock", "equity", "equity_us", "Investors Bancorp Inc."),
            ("LCNB",  "stock", "equity", "equity_us", "LCNB Corp."),
            ("LKFN",  "stock", "equity", "equity_us", "Lakeland Financial Corp."),
            ("MBIN",  "stock", "equity", "equity_us", "Merchants Financial Group Inc."),
            ("NBTB",  "stock", "equity", "equity_us", "NBT Bancorp Inc."),
            ("NFBK",  "stock", "equity", "equity_us", "Northfield Bancorp Inc."),
            ("NWBI",  "stock", "equity", "equity_us", "Northwest Bancshares Inc."),
            ("OFG",   "stock", "equity", "equity_us", "OFG Bancorp"),
            ("OPBK",  "stock", "equity", "equity_us", "OP Bancorp"),
            ("ORRF",  "stock", "equity", "equity_us", "Orrstown Financial Services Inc."),
            ("PEBO",  "stock", "equity", "equity_us", "Peoples Bancorp Inc."),
            ("PFIS",  "stock", "equity", "equity_us", "Peoples Financial Services Corp."),
            ("PRAA",  "stock", "equity", "equity_us", "PRA Group Inc."),
            ("QCRH",  "stock", "equity", "equity_us", "QCR Holdings Inc."),
            ("RBCAA", "stock", "equity", "equity_us", "Republic Bancorp Inc."),
            ("RDN",   "stock", "equity", "equity_us", "Radian Group Inc."),
            ("RNST",  "stock", "equity", "equity_us", "Renasant Corp."),
            ("SBCF",  "stock", "equity", "equity_us", "Seacoast Banking Corp. of Florida"),
            ("SFBS",  "stock", "equity", "equity_us", "ServisFirst Bancshares Inc."),
            ("SFNC",  "stock", "equity", "equity_us", "Simmons First National Corp."),
            ("SMBK",  "stock", "equity", "equity_us", "SmartFinancial Bancshares Inc."),
            ("SMBC",  "stock", "equity", "equity_us", "Southern Missouri Bancorp Inc."),
            ("STBA",  "stock", "equity", "equity_us", "S&T Bancorp Inc."),
            ("TBNK",  "stock", "equity", "equity_us", "Territorial Bancorp Inc."),
            ("TCBK",  "stock", "equity", "equity_us", "TriCo Bancshares"),
            ("TRMK",  "stock", "equity", "equity_us", "Trustmark Corp."),
            ("UBSI",  "stock", "equity", "equity_us", "United Bankshares Inc."),
            ("UCBI",  "stock", "equity", "equity_us", "United Community Banks Inc."),
            ("UVSP",  "stock", "equity", "equity_us", "Univest Financial Corp."),
            ("WAFD",  "stock", "equity", "equity_us", "Washington Federal Inc."),
            ("WSBC",  "stock", "equity", "equity_us", "WesBanco Inc."),
            # Consumer small caps
            ("BOOT",  "stock", "equity", "equity_us", "Boot Barn Holdings Inc."),
            ("CATO",  "stock", "equity", "equity_us", "Cato Corp."),
            ("CHUY",  "stock", "equity", "equity_us", "Chuy's Holdings Inc."),
            ("CVCO",  "stock", "equity", "equity_us", "Cavco Industries Inc."),
            ("EAT",   "stock", "equity", "equity_us", "Brinker International Inc."),
            ("ELF",   "stock", "equity", "equity_us", "e.l.f. Beauty Inc."),
            ("FIZZ",  "stock", "equity", "equity_us", "National Beverage Corp."),
            ("FRPT",  "stock", "equity", "equity_us", "Freshpet Inc."),
            ("GIII",  "stock", "equity", "equity_us", "G-III Apparel Group Ltd."),
            ("GPI",   "stock", "equity", "equity_us", "Group 1 Automotive Inc."),
            ("HIMS",  "stock", "equity", "equity_us", "Hims & Hers Health Inc."),
            ("IBP",   "stock", "equity", "equity_us", "Installed Building Products Inc."),
            ("JACK",  "stock", "equity", "equity_us", "Jack in the Box Inc."),
            ("JELD",  "stock", "equity", "equity_us", "JELD-WEN Holding Inc."),
            ("JOANN", "stock", "equity", "equity_us", "JOANN Inc."),
            ("KRUS",  "stock", "equity", "equity_us", "Kura Sushi USA Inc."),
            ("LACO",  "stock", "equity", "equity_us", "Lauro Cinco SA"),
            ("LCUT",  "stock", "equity", "equity_us", "Lifetime Brands Inc."),
            ("LESL",  "stock", "equity", "equity_us", "Leslie's Inc."),
            ("LXFR",  "stock", "equity", "equity_us", "Luxfer Holdings PLC"),
            ("MODG",  "stock", "equity", "equity_us", "Topgolf Callaway Brands Corp."),
            ("MOV",   "stock", "equity", "equity_us", "Movado Group Inc."),
            ("NBTX",  "stock", "equity", "equity_us", "Nanobiotix SA"),
            ("NKLA",  "stock", "equity", "equity_us", "Nikola Corp."),
            ("ODP",   "stock", "equity", "equity_us", "ODP Corp."),
            ("PATK",  "stock", "equity", "equity_us", "Patrick Industries Inc."),
            ("PLYA",  "stock", "equity", "equity_us", "Playa Hotels & Resorts NV"),
            ("PRTA",  "stock", "equity", "equity_us", "Prothena Corp. PLC"),
            ("PTLO",  "stock", "equity", "equity_us", "Portillo's Inc."),
            ("RICK",  "stock", "equity", "equity_us", "RCI Hospitality Holdings Inc."),
            ("RCUS",  "stock", "equity", "equity_us", "Arcus Biosciences Inc."),
            ("RUSHA", "stock", "equity", "equity_us", "Rush Enterprises Inc. Class A"),
            ("SAH",   "stock", "equity", "equity_us", "Sonic Automotive Inc."),
            ("SKIN",  "stock", "equity", "equity_us", "Beauty Health Co."),
            ("SLP",   "stock", "equity", "equity_us", "Simulations Plus Inc."),
            ("SMPL",  "stock", "equity", "equity_us", "Simply Good Foods Co."),
            ("SONO",  "stock", "equity", "equity_us", "Sonos Inc."),
            ("SPTN",  "stock", "equity", "equity_us", "SpartanNash Co."),
            ("STKS",  "stock", "equity", "equity_us", "ONE Group Hospitality Inc."),
            ("SWBI",  "stock", "equity", "equity_us", "Smith & Wesson Brands Inc."),
            ("THO",   "stock", "equity", "equity_us", "Thor Industries Inc."),
            ("TPIC",  "stock", "equity", "equity_us", "TPI Composites Inc."),
            ("UEIC",  "stock", "equity", "equity_us", "Universal Electronics Inc."),
            ("VSCO",  "stock", "equity", "equity_us", "Victoria's Secret & Co."),
            ("WERN",  "stock", "equity", "equity_us", "Werner Enterprises Inc."),
            ("WINA",  "stock", "equity", "equity_us", "Winmark Corp."),
            ("WOOF",  "stock", "equity", "equity_us", "Petco Health and Wellness Co. Inc."),
            ("WW",    "stock", "equity", "equity_us", "WW International Inc."),
            ("XPOF",  "stock", "equity", "equity_us", "Xponential Fitness Inc."),
            # Industrial small caps
            ("AGCO",  "stock", "equity", "equity_us", "AGCO Corp."),
            ("ARCB",  "stock", "equity", "equity_us", "ArcBest Corp."),
            ("ASTE",  "stock", "equity", "equity_us", "Astec Industries Inc."),
            ("B",     "stock", "equity", "equity_us", "Barnes Group Inc."),
            ("BFAM",  "stock", "equity", "equity_us", "Bright Horizons Family Solutions Inc."),
            ("BMI",   "stock", "equity", "equity_us", "Badger Meter Inc."),
            ("BRSS",  "stock", "equity", "equity_us", "Global Brass and Copper Holdings Inc."),
            ("CABO",  "stock", "equity", "equity_us", "Cable One Inc."),
            ("CECO",  "stock", "equity", "equity_us", "CECO Environmental Corp."),
            ("CENT",  "stock", "equity", "equity_us", "Central Garden and Pet Co."),
            ("CFX",   "stock", "equity", "equity_us", "Colfax Corp."),
            ("CMPO",  "stock", "equity", "equity_us", "CompoSecure Inc."),
            ("CNX",   "stock", "equity", "equity_us", "CNX Resources Corp."),
            ("CNXN",  "stock", "equity", "equity_us", "PC Connection Inc."),
            ("CRAI",  "stock", "equity", "equity_us", "CRA International Inc."),
            ("DENN",  "stock", "equity", "equity_us", "Denny's Corp."),
            ("DLB",   "stock", "equity", "equity_us", "Dolby Laboratories Inc."),
            ("EE",    "stock", "equity", "equity_us", "Excelerate Energy Inc."),
            ("EFC",   "stock", "equity", "equity_us", "Ellington Financial Inc."),
            ("ELME",  "stock", "equity", "equity_us", "Elme Communities"),
            ("ESE",   "stock", "equity", "equity_us", "ESCO Technologies Inc."),
            ("ESRT",  "stock", "equity", "equity_us", "Empire State Realty Trust Inc."),
            ("ETON",  "stock", "equity", "equity_us", "Eton Pharmaceuticals Inc."),
            ("EXPO",  "stock", "equity", "equity_us", "Exponent Inc."),
            ("FCNCA", "stock", "equity", "equity_us", "First Citizens BancShares Inc."),
            ("FELE",  "stock", "equity", "equity_us", "Franklin Electric Co. Inc."),
            ("FULT",  "stock", "equity", "equity_us", "Fulton Financial Corp."),
            ("GES",   "stock", "equity", "equity_us", "Guess?, Inc."),
            ("GFF",   "stock", "equity", "equity_us", "Griffon Corp."),
            ("GHM",   "stock", "equity", "equity_us", "Graham Corp."),
            ("GMS",   "stock", "equity", "equity_us", "GMS Inc."),
            ("HAFC",  "stock", "equity", "equity_us", "Hanmi Financial Corp."),
            ("HLX",   "stock", "equity", "equity_us", "Helix Energy Solutions Group Inc."),
            ("HNI",   "stock", "equity", "equity_us", "HNI Corp."),
            ("HRI",   "stock", "equity", "equity_us", "Herc Holdings Inc."),
            ("HSTM",  "stock", "equity", "equity_us", "HealthStream Inc."),
            ("HZO",   "stock", "equity", "equity_us", "MarineMax Inc."),
            ("IIIN",  "stock", "equity", "equity_us", "Insteel Industries Inc."),
            ("INT",   "stock", "equity", "equity_us", "World Fuel Services Corp."),
            ("JOBY",  "stock", "equity", "equity_us", "Joby Aviation Inc."),
            ("KAI",   "stock", "equity", "equity_us", "Kadant Inc."),
            ("KFY",   "stock", "equity", "equity_us", "Korn Ferry"),
            ("KMT",   "stock", "equity", "equity_us", "Kennametal Inc."),
            ("KNTK",  "stock", "equity", "equity_us", "Kinetik Holdings Inc."),
            ("LBRT",  "stock", "equity", "equity_us", "Liberty Energy Inc."),
            ("LNN",   "stock", "equity", "equity_us", "Lindsay Corp."),
            ("LQDT",  "stock", "equity", "equity_us", "Liquidity Services Inc."),
            ("LXU",   "stock", "equity", "equity_us", "LSB Industries Inc."),
            ("MATX",  "stock", "equity", "equity_us", "Matson Inc."),
            ("MBUU",  "stock", "equity", "equity_us", "Malibu Boats Inc."),
            ("MGRC",  "stock", "equity", "equity_us", "McGrath RentCorp"),
            ("MTRN",  "stock", "equity", "equity_us", "Materion Corp."),
            ("MYRG",  "stock", "equity", "equity_us", "MYR Group Inc."),
            ("NHC",   "stock", "equity", "equity_us", "National HealthCare Corp."),
            ("NMIH",  "stock", "equity", "equity_us", "NMI Holdings Inc."),
            ("NOVA",  "stock", "equity", "equity_us", "Sunnova Energy International Inc."),
            ("NRP",   "stock", "equity", "equity_us", "Natural Resource Partners LP"),
            ("NTCT",  "stock", "equity", "equity_us", "NetScout Systems Inc."),
            ("NWPX",  "stock", "equity", "equity_us", "Northwest Pipe Co."),
            ("OII",   "stock", "equity", "equity_us", "Oceaneering International Inc."),
            ("OMFL",  "stock", "equity", "equity_us", "Invesco Russell 1000 Dynamic Multifactor ETF"),
            ("OSIS",  "stock", "equity", "equity_us", "OSI Systems Inc."),
            ("POWI",  "stock", "equity", "equity_us", "Power Integrations Inc."),
            ("PPBI",  "stock", "equity", "equity_us", "Pacific Premier Bancorp Inc."),
            ("RDUS",  "stock", "equity", "equity_us", "Radius Recycling Inc."),
            ("RES",   "stock", "equity", "equity_us", "RPC Inc."),
            ("REVG",  "stock", "equity", "equity_us", "REV Group Inc."),
            ("RGR",   "stock", "equity", "equity_us", "Sturm Ruger & Co. Inc."),
            ("RLJ",   "stock", "equity", "equity_us", "RLJ Lodging Trust"),
            ("ROCK",  "stock", "equity", "equity_us", "Gibraltar Industries Inc."),
            ("RRGB",  "stock", "equity", "equity_us", "Red Robin Gourmet Burgers Inc."),
            ("RUSHA", "stock", "equity", "equity_us", "Rush Enterprises Inc. Class A"),
            ("SCSC",  "stock", "equity", "equity_us", "ScanSource Inc."),
            ("SEB",   "stock", "equity", "equity_us", "Seaboard Corp."),
            ("SGH",   "stock", "equity", "equity_us", "SMART Global Holdings Inc."),
            ("SPXC",  "stock", "equity", "equity_us", "SPX Technologies Inc."),
            ("TILE",  "stock", "equity", "equity_us", "Interface Inc."),
            ("TRMK",  "stock", "equity", "equity_us", "Trustmark Corp."),
            ("TRS",   "stock", "equity", "equity_us", "TriMas Corp."),
            ("TRST",  "stock", "equity", "equity_us", "TrustCo Bancorp NY"),
            ("UNF",   "stock", "equity", "equity_us", "UniFirst Corp."),
            ("USLM",  "stock", "equity", "equity_us", "United States Lime & Minerals Inc."),
            ("VSH",   "stock", "equity", "equity_us", "Vishay Intertechnology Inc."),
            ("WDFC",  "stock", "equity", "equity_us", "WD-40 Co."),
            ("WGO",   "stock", "equity", "equity_us", "Winnebago Industries Inc."),
            ("WNC",   "stock", "equity", "equity_us", "Wabash National Corp."),
            ("WRLD",  "stock", "equity", "equity_us", "World Acceptance Corp."),
            ("WSC",   "stock", "equity", "equity_us", "WillScot Mobile Mini Holdings Corp."),
        ],
    },
}

# Bundle IDs for validation in config endpoints
BUNDLE_IDS: frozenset[str] = frozenset(_BUNDLES)


def resync_universe(session: Session) -> int:
    """Recompute UniverseEntry from currently-enabled bundles.

    Instruments stay in universe if ANY of their bundle_tags is enabled.
    Returns the new universe size.
    """
    enabled: set[str] = {
        row.id for row in session.execute(
            select(BundleState).where(BundleState.enabled == True)  # noqa: E712
        ).scalars().all()
    }

    if not enabled:
        session.execute(delete(UniverseEntry))
        return 0

    # Filter instruments by bundle_tags in Python (SQLite JSON array queries are limited)
    all_instruments = session.execute(select(Instrument)).scalars().all()
    wanted_ids: set[int] = {
        inst.id for inst in all_instruments
        if any(tag in enabled for tag in (inst.bundle_tags or []))
    }

    existing: dict[int, UniverseEntry] = {
        row.instrument_id: row
        for row in session.execute(select(UniverseEntry)).scalars().all()
    }

    for iid, entry in existing.items():
        if iid not in wanted_ids:
            session.delete(entry)

    for iid in wanted_ids:
        if iid not in existing:
            session.add(UniverseEntry(instrument_id=iid))

    return len(wanted_ids)


@router.post("/seed-bundles", response_model=RefreshResult)
def seed_bundles(session: SessionDep) -> RefreshResult:
    """Upsert all bundle instruments and register BundleState entries.

    Idempotent. Does not enable any bundles — use PUT /api/config/bundles/{id}.
    Accumulates bundle_tags on instruments that appear in multiple bundles.
    """
    total_instruments = 0

    for bundle_id, bundle in _BUNDLES.items():
        count = 0
        for ticker, itype, asset_class, sleeve, name in bundle["instruments"]:
            inst = session.execute(
                select(Instrument).where(Instrument.ticker == ticker)
            ).scalar_one_or_none()
            if inst is None:
                inst = Instrument(
                    ticker=ticker, name=name,
                    instrument_type=itype, asset_class=asset_class, sleeve=sleeve,
                    is_cash_equivalent=False, needs_unwind=False,
                    aliases=[], bundle_tags=[bundle_id],
                )
                session.add(inst)
            else:
                tags: list[str] = list(inst.bundle_tags or [])
                if bundle_id not in tags:
                    tags.append(bundle_id)
                    inst.bundle_tags = tags
                if inst.sleeve in (None, "unclassified"):
                    inst.instrument_type = itype
                    inst.asset_class = asset_class
                    inst.sleeve = sleeve
            count += 1
        total_instruments += count
        session.flush()

        # Create BundleState if missing; preserve enabled state if already present
        state = session.get(BundleState, bundle_id)
        if state is None:
            session.add(BundleState(id=bundle_id, enabled=False, instrument_count=count))
        else:
            state.instrument_count = count

    universe_size = resync_universe(session)
    session.commit()

    bundle_names = ", ".join(_BUNDLES)
    return RefreshResult(
        ok=True,
        message=f"Seeded {total_instruments} instrument records across bundles: {bundle_names}. Universe: {universe_size} active.",
        count=total_instruments,
    )
