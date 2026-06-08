"""Oracle configuration: load YAML persona configs."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml


@dataclass
class OracleConfig:
    id: str
    display_name: str
    rivals: list[str]
    universe_filter: str | None  # None | "equity_only" | "fund_only"
    sleeve_targets: dict[str, Decimal]
    metric_weights: dict[str, float]
    persona_constraints: dict[str, object]
    abstain_floor: float
    # Macro Tactician-specific sleeve targets per tilt
    defensive_sleeve_targets: dict[str, Decimal] = field(default_factory=dict)
    aggressive_sleeve_targets: dict[str, Decimal] = field(default_factory=dict)
    regime_block_weights: dict[str, float] = field(default_factory=dict)


_CONFIGS_DIR = Path(__file__).parent / "configs"

ORACLE_IDS = [
    "value_purist",
    "growth_visionary",
    "yield_harvester",
    "macro_tactician",
    "quant",
    "passive_pragmatist",
]


def load_oracle_config(oracle_id: str) -> OracleConfig:
    path = _CONFIGS_DIR / f"{oracle_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Oracle config not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    return OracleConfig(
        id=data["id"],
        display_name=data["display_name"],
        rivals=data.get("rivals", []),
        universe_filter=data.get("universe_filter"),
        sleeve_targets={k: Decimal(str(v)) for k, v in data.get("sleeve_targets", {}).items()},
        metric_weights={k: float(v) for k, v in data.get("metric_weights", {}).items()},
        persona_constraints=data.get("persona_constraints") or {},
        abstain_floor=float(data.get("abstain_floor", 0.5)),
        defensive_sleeve_targets={
            k: Decimal(str(v))
            for k, v in data.get("defensive_sleeve_targets", {}).items()
        },
        aggressive_sleeve_targets={
            k: Decimal(str(v))
            for k, v in data.get("aggressive_sleeve_targets", {}).items()
        },
        regime_block_weights={
            k: float(v) for k, v in data.get("regime_block_weights", {}).items()
        },
    )


def load_all_oracle_configs() -> list[OracleConfig]:
    return [load_oracle_config(oid) for oid in ORACLE_IDS]
