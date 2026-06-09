"""Custom sleeve validation tests (AC-5, AC-6)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from committee.core.sleeves import (
    SleeveValidationError,
    create_sleeve_config,
    validate_sleeve_config,
)


def test_weights_must_sum_to_one(db_session):
    with pytest.raises(SleeveValidationError, match="sum"):
        create_sleeve_config(
            session=db_session,
            name="test_config",
            sleeves=[
                {"key": "equity", "label": "Equity", "target_weight": "0.50", "sort_order": 0},
                {"key": "bonds", "label": "Bonds", "target_weight": "0.40", "sort_order": 1},
            ],
            assignments={},
        )


def test_weights_sum_exactly_to_one(db_session):
    config = create_sleeve_config(
        session=db_session,
        name="two_sleeve",
        sleeves=[
            {"key": "equity", "label": "Equity", "target_weight": "0.6000", "sort_order": 0},
            {"key": "bonds", "label": "Bonds", "target_weight": "0.4000", "sort_order": 1},
        ],
        assignments={},
    )
    assert config.id is not None
    assert config.version == 1


def test_duplicate_sleeve_key_rejected(db_session):
    with pytest.raises(SleeveValidationError):
        create_sleeve_config(
            session=db_session,
            name="dup_key_test",
            sleeves=[
                {"key": "equity", "label": "Equity A", "target_weight": "0.5000", "sort_order": 0},
                {"key": "equity", "label": "Equity B", "target_weight": "0.5000", "sort_order": 1},
            ],
            assignments={},
        )


def test_assignment_to_unknown_sleeve_key_rejected(db_session):
    with pytest.raises(SleeveValidationError):
        create_sleeve_config(
            session=db_session,
            name="bad_assign",
            sleeves=[
                {"key": "equity", "label": "Equity", "target_weight": "1.0000", "sort_order": 0},
            ],
            assignments={1: "nonexistent_sleeve"},
        )


def test_historical_config_version_preserved(db_session):
    """Creating config with same name increments version; old version label preserved."""
    v1 = create_sleeve_config(
        db_session, "my_config",
        sleeves=[
            {"key": "eq", "label": "Equity v1", "target_weight": "1.0000", "sort_order": 0},
        ],
        assignments={},
    )
    db_session.flush()
    assert v1.version == 1

    v2 = create_sleeve_config(
        db_session, "my_config",
        sleeves=[
            {"key": "eq", "label": "Equity v2", "target_weight": "1.0000", "sort_order": 0},
        ],
        assignments={},
    )
    db_session.flush()
    assert v2.version == 2

    # v1 label is preserved (immutable)
    db_session.refresh(v1)
    v1_def = v1.definitions[0]
    assert v1_def.label == "Equity v1"

    # v1 is now inactive
    assert v1.status == "inactive"
    assert v2.status == "active"


def test_new_version_replaces_active(db_session):
    """Active config is replaced by new version; only one active at a time."""
    from committee.core.sleeves import get_active_config
    create_sleeve_config(db_session, "rolling", sleeves=[
        {"key": "all", "label": "All", "target_weight": "1.0000", "sort_order": 0},
    ], assignments={})
    db_session.flush()
    create_sleeve_config(db_session, "rolling", sleeves=[
        {"key": "all", "label": "All v2", "target_weight": "1.0000", "sort_order": 0},
    ], assignments={})
    db_session.flush()
    active = get_active_config(db_session, "rolling")
    assert active is not None
    assert active.version == 2
