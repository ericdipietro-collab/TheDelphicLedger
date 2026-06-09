"""Ingest endpoints: two-step CSV import from the dashboard.

Step 1 — POST /api/ingest/stage  (multipart upload)
  Detects file type, maps headers using saved templates or proposals.
  Stores file bytes in a short-lived in-memory staging area keyed by file_hash.
  Returns metadata + column mapping for the user to review.

Step 2 — POST /api/ingest/confirm
  Accepts file_hash + optional column_map override.
  Persists the import and runs auto-resolution.
"""

from __future__ import annotations

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from committee.api.deps import get_session
from committee.ingest.importer import ImportResult, process_file
from committee.ingest.persist import persist_import
from committee.ingest.template import MappingTemplate, save_template
from committee.resolver.cascade import resolve_instrument
from committee.resolver.openfigi import live_figi_lookup
from committee.models import PositionSnapshot

router = APIRouter(prefix="/api/ingest", tags=["ingest"])
SessionDep = Annotated[Session, Depends(get_session)]

# ── In-memory staging store (local single-user app, cleared after confirm) ───

_staging: dict[str, bytes] = {}
_staging_lock = threading.Lock()

_MAX_STAGE_BYTES = 10 * 1024 * 1024  # 10 MB


# ── Response models ───────────────────────────────────────────────────────────

class ColumnProposal(BaseModel):
    source_col: str
    canonical_field: str | None
    method: str
    score: float | None


class StageResult(BaseModel):
    file_hash: str
    filename: str
    file_type: str
    row_count: int
    template_name: str | None
    known_template: bool
    column_map: dict[str, str | None]
    proposals: list[ColumnProposal]
    queued_types: list[str]


class ConfirmRequest(BaseModel):
    file_hash: str
    file_type: str | None = None
    column_map: dict[str, str | None] | None = None
    save_as_template: str | None = None  # human name; omit to skip saving


class ConfirmResult(BaseModel):
    batch_id: int
    row_count: int
    file_type: str
    resolved_count: int
    queued_count: int
    template_saved: str | None = None  # name if a template was saved


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/stage", response_model=StageResult)
async def stage_import(file: UploadFile = File(...)) -> StageResult:
    """Upload a CSV and detect its format. Returns column mapping for review."""
    content = await file.read()

    if len(content) > _MAX_STAGE_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB).")
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")

    filename = file.filename or "upload.csv"

    try:
        result: ImportResult = process_file(content=content, filename=filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    with _staging_lock:
        _staging[result.file_hash] = content

    return StageResult(
        file_hash=result.file_hash,
        filename=result.filename,
        file_type=result.file_type,
        row_count=result.row_count,
        template_name=result.template_name,
        known_template=result.template_name is not None,
        column_map=result.column_map,
        proposals=[
            ColumnProposal(
                source_col=p.source_col,
                canonical_field=p.canonical_field,
                method=p.method,
                score=p.score,
            )
            for p in result.proposals
        ],
        queued_types=result.queued_types,
    )


@router.post("/confirm", response_model=ConfirmResult)
def confirm_import(req: ConfirmRequest, session: SessionDep) -> ConfirmResult:
    """Persist a staged import and run auto-resolution."""
    with _staging_lock:
        content = _staging.get(req.file_hash)

    if content is None:
        raise HTTPException(
            status_code=404,
            detail="Staged file not found. Upload the file again via /api/ingest/stage.",
        )

    try:
        result: ImportResult = process_file(
            content=content,
            filename="upload.csv",
            column_map_override=req.column_map,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if req.file_type and req.file_type in ("positions", "transactions"):
        result = result.model_copy(update={"file_type": req.file_type})

    try:
        batch = persist_import(result, session)
        session.commit()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    # Save mapping template if the user named this format
    template_saved: str | None = None
    if req.save_as_template:
        tpl_name = req.save_as_template.strip()
        if tpl_name:
            tpl = MappingTemplate(
                name=tpl_name,
                fingerprint=result.broker_fingerprint,
                file_type=result.file_type,
                column_map=result.column_map,
            )
            save_template(tpl)
            template_saved = tpl_name

    # Auto-resolve for position imports (same logic as CLI)
    resolved = queued = 0
    if result.file_type == "positions":
        from sqlalchemy import select
        snapshots = session.execute(
            select(PositionSnapshot).where(PositionSnapshot.batch_id == batch.id)
        ).scalars().all()
        seen: set[str] = set()
        for snap in snapshots:
            raw = snap.raw_instrument
            if not raw or raw in seen:
                continue
            seen.add(raw)
            res = resolve_instrument(
                raw_ticker=raw,
                raw_name=None,
                session=session,
                batch_id=batch.id,
                figi_lookup=live_figi_lookup,
            )
            if res.queued:
                queued += 1
            else:
                resolved += 1
        session.commit()

    # Rebuild holdings from resolved snapshots so oracles can score on next convene
    from committee.holdings import rebuild_holdings
    rebuild_holdings(session)
    session.commit()

    # Derive lots (derived data — skipped if M6 not yet built)
    try:
        from committee.lots.derive import derive_lots
        derive_lots(session)
        session.commit()
    except ImportError:
        pass

    # Remove from staging
    with _staging_lock:
        _staging.pop(req.file_hash, None)

    return ConfirmResult(
        batch_id=batch.id,
        row_count=result.row_count,
        file_type=result.file_type,
        resolved_count=resolved,
        queued_count=queued,
        template_saved=template_saved,
    )
