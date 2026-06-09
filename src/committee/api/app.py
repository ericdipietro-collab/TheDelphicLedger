"""FastAPI application — read-only dashboard API, bound to 127.0.0.1.

All routes are prefixed /api/. The built React frontend is served as static
files from dashboard/dist/ when present.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from committee.api.routes import (
    actions,
    chamber,
    config,
    data,
    ingest,
    instruments,
    portfolio,
    recon,
    scenarios,
    trades,
)
from committee.api.routes.sleeves import router as sleeves_router


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    yield


app = FastAPI(
    title="Delphic Ledger",
    description="Read-only portfolio analysis dashboard. Locally run, 127.0.0.1 only.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS locked to localhost origins only (Invariant F).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:7777", "http://127.0.0.1:7777"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# API routers
app.include_router(actions.router)
app.include_router(data.router)
app.include_router(ingest.router)
app.include_router(instruments.router)
app.include_router(chamber.router)
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(scenarios.router)
app.include_router(recon.router)
app.include_router(config.router)
app.include_router(sleeves_router)

# Serve built frontend if present
_DIST = Path(__file__).parent.parent.parent.parent / "dashboard" / "dist"

if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        """Serve dist files directly when they exist; fall back to index.html for SPA routing."""
        candidate = _DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_DIST / "index.html"))
