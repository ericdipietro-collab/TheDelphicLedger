"""Mapping templates: persist and load broker column-map profiles."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

_PROFILES_DIR = Path("profiles")


class MappingTemplate(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    fingerprint: str
    file_type: str  # "positions" | "transactions"
    column_map: dict[str, str | None]
    type_aliases: dict[str, str] = {}


def compute_fingerprint(headers: list[str]) -> str:
    """Order-independent SHA-256[:16] of normalized headers."""
    key = "|".join(sorted(h.strip().lower() for h in headers))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _template_path(fingerprint: str) -> Path:
    return _PROFILES_DIR / f"{fingerprint}.yaml"


def load_template(fingerprint: str, profiles_dir: Path = _PROFILES_DIR) -> MappingTemplate | None:
    path = profiles_dir / f"{fingerprint}.yaml"
    if not path.exists():
        return None
    with path.open() as f:
        data: Any = yaml.safe_load(f)
    return MappingTemplate.model_validate(data)


def save_template(template: MappingTemplate, profiles_dir: Path = _PROFILES_DIR) -> Path:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    path = profiles_dir / f"{template.fingerprint}.yaml"
    with path.open("w") as f:
        yaml.dump(template.model_dump(), f, default_flow_style=False, sort_keys=True)
    return path


def load_named_template(name: str, profiles_dir: Path = _PROFILES_DIR) -> MappingTemplate | None:
    """Load a template by its human name (scans all profiles)."""
    if not profiles_dir.exists():
        return None
    for path in profiles_dir.glob("*.yaml"):
        with path.open() as f:
            data: Any = yaml.safe_load(f)
        if data and data.get("name") == name:
            return MappingTemplate.model_validate(data)
    return None


def export_template_yaml(template: MappingTemplate) -> str:
    return yaml.dump(template.model_dump(), default_flow_style=False, sort_keys=True)


def import_template_yaml(yaml_str: str) -> MappingTemplate:
    data: Any = yaml.safe_load(yaml_str)
    return MappingTemplate.model_validate(data)
