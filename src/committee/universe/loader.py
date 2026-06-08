"""Load bundle configurations from bundles/*.yaml files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from committee.universe.bundle import BundleConfig, TickerEntry


def load_bundle_configs(bundles_dir: Path) -> list[BundleConfig]:
    """Load all bundle YAML files from the given directory."""
    if not bundles_dir.exists():
        return []
    configs = []
    for path in sorted(bundles_dir.glob("*.yaml")):
        with path.open() as f:
            data: Any = yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            continue
        # Normalise tickers: accept both plain strings and {ticker, sleeve} dicts
        raw_tickers: list[Any] = data.get("tickers", [])
        tickers = []
        for t in raw_tickers:
            if isinstance(t, str):
                tickers.append(TickerEntry(ticker=t))
            elif isinstance(t, dict) and "ticker" in t:
                tickers.append(TickerEntry(**{k: v for k, v in t.items() if k in ("ticker", "sleeve")}))
        data["tickers"] = tickers
        configs.append(BundleConfig.model_validate(data))
    return configs


def load_bundle_config(bundle_id: str, bundles_dir: Path) -> BundleConfig | None:
    """Load a single bundle by id."""
    for cfg in load_bundle_configs(bundles_dir):
        if cfg.id == bundle_id:
            return cfg
    return None
