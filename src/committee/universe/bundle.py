"""Bundle configuration model (loaded from bundles/*.yaml)."""

from __future__ import annotations

from pydantic import BaseModel


class TickerEntry(BaseModel):
    ticker: str
    sleeve: str | None = None


class IndexProxyConfig(BaseModel):
    url: str
    template: str  # name of mapping template in profiles/


class BundleConfig(BaseModel):
    id: str
    description: str
    source: str  # "explicit" | "index_proxy" | "mixed"
    refresh_policy: str = "nightly"  # "nightly" | "on_demand"
    sleeve_default: str | None = None
    enabled_default: bool = False
    size_estimate: int | None = None
    tickers: list[TickerEntry] = []
    index_proxy: IndexProxyConfig | None = None
