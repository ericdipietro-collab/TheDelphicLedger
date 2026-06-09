"""Instrument resolution endpoints: unresolved queue review and mapping."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.models import Holding, Instrument, MappingDecision, UnresolvedQueue

router = APIRouter(prefix="/api/instruments", tags=["instruments"])
SessionDep = Annotated[Session, Depends(get_session)]


# ── Response models ───────────────────────────────────────────────────────────

class UnresolvedItem(BaseModel):
    id: int
    raw_value: str
    context: dict | None


class InstrumentSummary(BaseModel):
    id: int
    ticker: str | None
    name: str | None
    instrument_type: str | None
    asset_class: str | None
    has_holdings: bool


class ResolveRequest(BaseModel):
    raw_value: str
    action: str  # "map" | "create" | "cash" | "skip"
    instrument_id: int | None = None   # for action="map"
    ticker: str | None = None          # for action="create"
    name: str | None = None            # for action="create"
    instrument_type: str = "unclassified"  # for action="create"


class ResolveResult(BaseModel):
    ok: bool
    message: str
    instrument_id: int | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _infer_asset_class(itype: str, name: str | None) -> str:
    name_lower = (name or "").lower()
    if itype == "cash":
        return "cash"
    if itype == "etf":
        if any(k in name_lower for k in ("bond", "fixed", "treasury", "yield", "income")):
            return "fixed_income"
        return "equity"
    if itype == "mutual_fund":
        return "equity"
    return "equity"


def _add_alias(inst: Instrument, raw: str) -> None:
    aliases: list[str] = list(inst.aliases or [])
    if raw not in aliases:
        aliases.append(raw)
    inst.aliases = aliases


def _get_or_create_cash(session: Session) -> Instrument:
    cash = session.execute(
        select(Instrument).where(Instrument.is_cash_equivalent == True)  # noqa: E712
    ).scalar_one_or_none()
    if cash is None:
        cash = Instrument(
            ticker="CASH",
            name="Cash & Cash Equivalents",
            instrument_type="cash",
            asset_class="cash",
            sleeve="cash",
            is_cash_equivalent=True,
            needs_unwind=False,
            aliases=[],
            bundle_tags=[],
        )
        session.add(cash)
        session.flush()
    return cash


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/unresolved", response_model=list[UnresolvedItem])
def list_unresolved(session: SessionDep) -> list[UnresolvedItem]:
    """All instrument queue items that haven't been resolved yet."""
    items = session.execute(
        select(UnresolvedQueue)
        .where(UnresolvedQueue.queue_type == "instrument")
        .where(UnresolvedQueue.resolved_at.is_(None))
        .order_by(UnresolvedQueue.id)
    ).scalars().all()
    return [
        UnresolvedItem(id=i.id, raw_value=i.raw_value, context=i.context_json)
        for i in items
    ]


@router.get("/search", response_model=list[InstrumentSummary])
def search_instruments(q: str = "", session: Session = Depends(get_session)) -> list[InstrumentSummary]:
    """Search instruments by ticker or name substring."""
    stmt = select(Instrument)
    if q:
        q_upper = q.upper()
        q_lower = q.lower()
        all_insts = session.execute(stmt).scalars().all()
        matched = [
            i for i in all_insts
            if (i.ticker and q_upper in i.ticker.upper())
            or (i.name and q_lower in i.name.lower())
        ]
    else:
        matched = session.execute(stmt.order_by(Instrument.ticker)).scalars().all()

    held_ids = {
        row[0] for row in session.execute(select(Holding.instrument_id).distinct()).all()
    }
    return [
        InstrumentSummary(
            id=i.id,
            ticker=i.ticker,
            name=i.name,
            instrument_type=i.instrument_type,
            asset_class=i.asset_class,
            has_holdings=i.id in held_ids,
        )
        for i in matched[:50]
    ]


@router.post("/resolve", response_model=ResolveResult)
def resolve_item(req: ResolveRequest, session: SessionDep) -> ResolveResult:
    """Resolve one unresolved instrument: map, create, mark-cash, or skip."""
    from datetime import datetime
    from decimal import Decimal

    queue_item = session.execute(
        select(UnresolvedQueue)
        .where(UnresolvedQueue.queue_type == "instrument")
        .where(UnresolvedQueue.raw_value == req.raw_value)
        .where(UnresolvedQueue.resolved_at.is_(None))
    ).scalar_one_or_none()

    if req.action == "skip":
        # Leave in queue but acknowledge — just return ok
        return ResolveResult(ok=True, message=f"Skipped {req.raw_value}.")

    resolved_inst: Instrument | None = None

    if req.action == "map":
        if req.instrument_id is None:
            raise HTTPException(status_code=422, detail="instrument_id required for action=map")
        resolved_inst = session.get(Instrument, req.instrument_id)
        if resolved_inst is None:
            raise HTTPException(status_code=404, detail=f"Instrument {req.instrument_id} not found")

    elif req.action == "create":
        ticker = (req.ticker or "").strip().upper() or None
        name = (req.name or "").strip() or ticker
        itype = req.instrument_type if req.instrument_type in ("stock", "etf", "mutual_fund", "cash") else "unclassified"
        resolved_inst = Instrument(
            ticker=ticker,
            name=name,
            instrument_type=itype,
            asset_class=_infer_asset_class(itype, name),
            sleeve="unclassified",
            is_cash_equivalent=(itype == "cash"),
            needs_unwind=False,
            aliases=[],
            bundle_tags=[],
        )
        session.add(resolved_inst)
        session.flush()

    elif req.action == "cash":
        resolved_inst = _get_or_create_cash(session)

    else:
        raise HTTPException(status_code=422, detail=f"Unknown action '{req.action}'")

    # Link alias and write audit record
    _add_alias(resolved_inst, req.raw_value)
    session.add(MappingDecision(
        batch_id=queue_item.context_json.get("batch_id") if queue_item and queue_item.context_json else None,
        raw_value=req.raw_value,
        resolved_to=resolved_inst.ticker,
        method="human",
        confidence=Decimal("1"),
        accepted_by="human",
    ))

    if queue_item is not None:
        queue_item.resolved_at = datetime.now()
        queue_item.resolved_to = resolved_inst.ticker

    # Rebuild holdings so new resolution is reflected immediately
    # (rebuild_holdings resolves via ticker/alias match, so no snapshot mutation needed)
    from committee.holdings import rebuild_holdings
    rebuild_holdings(session)
    session.commit()

    return ResolveResult(
        ok=True,
        message=f"Resolved '{req.raw_value}' → {resolved_inst.ticker or resolved_inst.name}",
        instrument_id=resolved_inst.id,
    )
