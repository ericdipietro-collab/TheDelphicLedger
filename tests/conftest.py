from __future__ import annotations

from pathlib import Path

import pytest

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
