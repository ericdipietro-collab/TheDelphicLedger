"""Universe management: enable, disable, refresh, status."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from committee.ingest.importer import process_file
from committee.ingest.template import load_named_template
from committee.models import BundleState, Instrument
from committee.universe.bundle import BundleConfig, TickerEntry
from committee.universe.guard import _DEFAULT_CAP, check_guard


def enable_bundle(
    session: Session,
    bundle: BundleConfig,
    cap: int = _DEFAULT_CAP,
) -> tuple[str, str]:
    """Enable a bundle. Returns (status, message) where status is 'ok', 'warn', or 'refuse'."""
    size = bundle.size_estimate or 0
    status, projected = check_guard(session, new_count=size, cap=cap)
    if status == "refuse":
        return (
            "refuse",
            f"Enabling '{bundle.id}' would grow the universe to ~{projected} instruments "
            f"(cap={cap}). Disable a bundle first.",
        )

    state = _get_or_create_state(session, bundle.id)
    state.enabled = True
    session.flush()

    suffix = f" [universe ~{projected}/{cap}]" if status == "warn" else ""
    return "ok", f"Bundle '{bundle.id}' enabled.{suffix}"


def disable_bundle(session: Session, bundle_id: str) -> str:
    state = session.get(BundleState, bundle_id)
    if state is None:
        return f"Bundle '{bundle_id}' not found in database (may not have been enabled yet)."
    state.enabled = False
    session.flush()
    return f"Bundle '{bundle_id}' disabled."


def refresh_bundle(
    session: Session,
    bundle: BundleConfig,
    profiles_dir: Path,
    http_get: object | None = None,
) -> tuple[int, str | None]:
    """Resolve + tag all instruments for a bundle.

    http_get: injectable for tests — callable(url: str) -> bytes.
    Returns (instrument_count_tagged, error_message | None).
    """
    if bundle.source in ("explicit", "mixed"):
        entries = bundle.tickers
    elif bundle.source == "index_proxy":
        try:
            raw = _fetch_index_tickers(bundle, profiles_dir, http_get)
        except Exception as exc:
            error = str(exc)
            state = _get_or_create_state(session, bundle.id)
            state.last_error = error
            session.flush()
            return 0, error
        entries = [TickerEntry(ticker=t, sleeve=bundle.sleeve_default) for t in raw]
    else:
        return 0, f"Unknown source type: {bundle.source!r}"

    count = 0
    for entry in entries:
        ticker = (entry.ticker or "").strip()
        if not ticker:
            continue
        sleeve_override = entry.sleeve or bundle.sleeve_default
        inst = _resolve_or_create(session, ticker, sleeve_override)
        if bundle.id not in (inst.bundle_tags or []):
            tags = list(inst.bundle_tags or [])
            tags.append(bundle.id)
            inst.bundle_tags = tags
            flag_modified(inst, "bundle_tags")
            count += 1

    state = _get_or_create_state(session, bundle.id)
    state.last_refreshed_at = datetime.now()
    state.instrument_count = count
    state.last_error = None
    session.flush()
    return count, None


def get_all_states(session: Session) -> dict[str, BundleState]:
    rows = session.execute(select(BundleState)).scalars().all()
    return {r.id: r for r in rows}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_or_create_state(session: Session, bundle_id: str) -> BundleState:
    state = session.get(BundleState, bundle_id)
    if state is None:
        state = BundleState(id=bundle_id, enabled=False, instrument_count=0)
        session.add(state)
        session.flush()
    return state


def _resolve_or_create(session: Session, ticker: str, sleeve_override: str | None = None) -> Instrument:
    """Find an instrument by exact ticker, or create a bare record if absent.

    If sleeve_override is given and the instrument is still unclassified, apply it.
    Already-classified instruments are never modified.
    """
    inst = session.execute(
        select(Instrument).where(Instrument.ticker == ticker)
    ).scalar_one_or_none()
    if inst is not None:
        if sleeve_override and inst.sleeve in ("unclassified", None):
            inst.sleeve = sleeve_override
        return inst
    inst = Instrument(
        ticker=ticker,
        name=None,
        instrument_type="unclassified",
        asset_class="unclassified",
        sleeve=sleeve_override or "unclassified",
        is_cash_equivalent=False,
        needs_unwind=False,
        aliases=[],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


def _fetch_index_tickers(
    bundle: BundleConfig,
    profiles_dir: Path,
    http_get: object | None,
) -> list[str]:
    if bundle.index_proxy is None:
        return []

    template = load_named_template(bundle.index_proxy.template, profiles_dir)
    if template is None:
        raise ValueError(
            f"Mapping template '{bundle.index_proxy.template}' not found in {profiles_dir}"
        )

    url = bundle.index_proxy.url
    if http_get is not None:
        content: bytes = http_get(url)  # type: ignore[operator]
    else:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        content = resp.content

    result = process_file(
        content=content,
        filename=f"{bundle.id}_holdings.csv",
        template_override=template,
        profiles_dir=profiles_dir,
    )
    return [row.instrument for row in result.positions if row.instrument]
