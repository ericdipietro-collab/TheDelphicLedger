"""FastAPI dependencies: DB session factory wired at serve-time."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

_engine: Engine | None = None


def init_engine(db_path: Path) -> None:
    global _engine
    _engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})


def get_session() -> Generator[Session, None, None]:
    if _engine is None:
        db_path = Path(os.environ.get("COMMITTEE_DB", "data/ledger.db"))
        init_engine(db_path)
    with Session(_engine) as session:  # type: ignore[arg-type]
        yield session
