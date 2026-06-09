"""Custom sleeve configuration — validation and creation.

Invariant: SleeveConfig rows are immutable once committed. To change a config,
create a new version via create_sleeve_config with the same name.
"""
from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import SleeveAssignment, SleeveConfig, SleeveDefinition

_WEIGHT_TOLERANCE = Decimal("0.0001")


class SleeveValidationError(ValueError):
    pass


def validate_sleeve_config(
    sleeves: list[dict[str, object]],
    assignments: dict[int, str],
) -> None:
    """Validate sleeve weights sum to 1.0 and no duplicate instrument assignments.

    Raises SleeveValidationError on any violation.
    """
    if not sleeves:
        raise SleeveValidationError("sleeve list must not be empty")

    total = sum(Decimal(str(s["target_weight"])) for s in sleeves)
    if abs(total - Decimal("1")) > _WEIGHT_TOLERANCE:
        raise SleeveValidationError(
            f"sleeve weights must sum to 1.0000 (got {total})"
        )

    keys = [s["key"] for s in sleeves]
    if len(keys) != len(set(keys)):
        raise SleeveValidationError("duplicate sleeve_key in sleeve list")

    valid_keys = set(keys)
    for _iid, key in assignments.items():
        if key not in valid_keys:
            raise SleeveValidationError(f"assignment references unknown sleeve key {key!r}")


def create_sleeve_config(
    session: Session,
    name: str,
    sleeves: list[dict[str, object]],
    assignments: dict[int, str],
) -> SleeveConfig:
    """Create a new versioned sleeve config. Validates before writing.

    If a config with the same name already exists, increments the version.
    Old configs with the same name are set to status='inactive'.
    """
    validate_sleeve_config(sleeves, assignments)

    existing = session.execute(
        select(SleeveConfig)
        .where(SleeveConfig.name == name, SleeveConfig.status == "active")
        .order_by(SleeveConfig.version.desc())
    ).scalars().all()

    next_version = 1
    if existing:
        next_version = existing[0].version + 1
        for old in existing:
            old.status = "inactive"

    config = SleeveConfig(name=name, version=next_version, status="active")
    session.add(config)
    session.flush()  # get config.id

    for s in sleeves:
        session.add(SleeveDefinition(
            config_id=config.id,
            sleeve_key=s["key"],
            label=s["label"],
            target_weight=Decimal(str(s["target_weight"])).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_EVEN
            ),
            sort_order=s.get("sort_order", 0),
        ))

    for instrument_id, sleeve_key in assignments.items():
        session.add(SleeveAssignment(
            config_id=config.id,
            instrument_id=instrument_id,
            sleeve_key=sleeve_key,
        ))

    return config


def get_active_config(session: Session, name: str) -> SleeveConfig | None:
    """Return the active (latest) config for a given name."""
    return session.execute(
        select(SleeveConfig)
        .where(SleeveConfig.name == name, SleeveConfig.status == "active")
    ).scalar_one_or_none()


def list_configs(session: Session) -> list[SleeveConfig]:
    """Return all configs (all statuses) ordered by name, version desc."""
    return list(
        session.execute(
            select(SleeveConfig).order_by(SleeveConfig.name, SleeveConfig.version.desc())
        )
        .scalars()
        .all()
    )
