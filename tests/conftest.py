from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from committee.models import Base

EXAMPLES = Path(__file__).parent.parent / "examples"
PROFILES = Path(__file__).parent.parent / "profiles"
GOLDENS = Path(__file__).parent / "goldens"


@pytest.fixture
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def profiles_dir() -> Path:
    return PROFILES


@pytest.fixture
def goldens_dir() -> Path:
    GOLDENS.mkdir(exist_ok=True)
    return GOLDENS


@pytest.fixture
def db_session() -> Session:
    """In-memory SQLite session with a fresh schema for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()
    engine.dispose()
