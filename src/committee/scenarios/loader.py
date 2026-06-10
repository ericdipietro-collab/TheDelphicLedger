"""Load scenario packs from YAML files in the scenarios/ directory."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from committee.core.types import ScenarioContext

_DEFAULT_SCENARIOS_DIR = Path("scenarios")


@dataclass
class ScenarioPack:
    pack_id: str
    display_name: str
    pack_type: str          # "historical" | "hypothetical"
    honesty_note: str
    sources: list[str]
    context: ScenarioContext


def load_pack(
    pack_id: str,
    scenarios_dir: Path = _DEFAULT_SCENARIOS_DIR,
) -> ScenarioPack:
    """Load a scenario pack by ID. Raises FileNotFoundError if the pack does not exist."""
    base = scenarios_dir.resolve()
    path = (base / f"{pack_id}.yaml").resolve()
    if not path.is_relative_to(base):
        raise FileNotFoundError(f"Scenario pack '{pack_id!r}' not found.")
    if not path.exists():
        raise FileNotFoundError(
            f"Scenario pack '{pack_id}' not found at {path}. "
            f"Available: {list_packs(scenarios_dir)}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    sleeve_shocks = {
        k: Decimal(str(v)) for k, v in raw.get("sleeve_shocks", {}).items()
    }
    indicator_overrides = {
        k: float(v) for k, v in raw.get("indicator_overrides", {}).items()
    }

    return ScenarioPack(
        pack_id=raw["id"],
        display_name=raw["display_name"],
        pack_type=raw.get("type", "historical"),
        honesty_note=raw.get("honesty_note", "").strip(),
        sources=list(raw.get("sources", [])),
        context=ScenarioContext(
            pack_id=raw["id"],
            sleeve_shocks=sleeve_shocks,
            indicator_overrides=indicator_overrides,
        ),
    )


def list_packs(scenarios_dir: Path = _DEFAULT_SCENARIOS_DIR) -> list[str]:
    """Return sorted list of available pack IDs."""
    if not scenarios_dir.exists():
        return []
    return sorted(p.stem for p in scenarios_dir.glob("*.yaml"))
