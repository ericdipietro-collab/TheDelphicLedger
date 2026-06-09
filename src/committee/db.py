from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from committee.models import Base

_DEFAULT_DB = Path("data") / "ledger.db"
_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def init_db(db_path: Path = _DEFAULT_DB) -> Engine:
    global _engine, _Session
    db_path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, echo=False)

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn: object, _: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[union-attr]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)

    # Ensure performance indexes exist on the live DB (idempotent — IF NOT EXISTS).
    # create_all won't add indexes to pre-existing tables, so we apply them explicitly.
    with engine.connect() as conn:
        conn.execute(__import__("sqlalchemy").text(
            "CREATE INDEX IF NOT EXISTS ix_mo_instrument_unit_date"
            " ON market_observations (instrument_id, unit, observed_date)"
        ))
        conn.execute(__import__("sqlalchemy").text(
            "CREATE INDEX IF NOT EXISTS ix_mo_series_date"
            " ON market_observations (series_id, observed_date)"
        ))
        conn.commit()

    _engine = engine
    _Session = sessionmaker(bind=engine)
    return engine


def get_session() -> Generator[Session, None, None]:
    if _Session is None:
        raise RuntimeError("call init_db() first")
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
