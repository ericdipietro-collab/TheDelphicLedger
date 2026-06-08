"""Instrument resolution cascade.

Strictly ordered: exact ticker → normalized ticker → alias table →
name fuzzy (≥93 auto, 80–92 queue-with-candidates, <80 bare queue) →
OpenFIGI (injectable) → human queue.

Every step that produces an outcome writes a MappingDecision audit row.
Nothing below the auto-threshold (93) is ever silently accepted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from committee.models import Instrument, MappingDecision, UnresolvedQueue
from committee.resolver.aliases import SWEEP_ALIASES, get_or_create_cash
from committee.resolver.classify import (
    classify_from_figi_type,
    classify_from_name,
    infer_asset_class,
    needs_unwind_flag,
)
from committee.resolver.normalize import normalize_ticker
from committee.resolver.openfigi import FigiResult

AUTO_THRESHOLD = 93
QUEUE_THRESHOLD = 80

FigiLookupFn = Callable[[str], FigiResult | None]


@dataclass
class ResolveResult:
    instrument_id: int | None
    method: str
    confidence: Decimal
    queued: bool = False
    candidates: list[tuple[int, int]] = field(default_factory=list)  # (instrument_id, score)


def resolve_instrument(
    raw_ticker: str | None,
    raw_name: str | None,
    session: Session,
    batch_id: int | None = None,
    figi_lookup: FigiLookupFn | None = None,
) -> ResolveResult:
    """Run the resolution cascade for one raw instrument value.

    Side effects: writes MappingDecision rows; may write to UnresolvedQueue;
    may create Instrument rows (alias table, OpenFIGI hit).
    Does NOT update any immutable tables (position_snapshots, transactions).
    """
    raw_label = raw_ticker or raw_name or ""

    # ── Step 1: exact ticker ──────────────────────────────────────────────────
    if raw_ticker:
        inst = session.execute(
            select(Instrument).where(Instrument.ticker == raw_ticker)
        ).scalar_one_or_none()
        if inst is not None:
            _add_alias(inst, raw_ticker)
            _write_decision(session, batch_id, raw_ticker, inst.ticker, "exact", Decimal("1"))
            return ResolveResult(instrument_id=inst.id, method="exact", confidence=Decimal("1"))

    # ── Step 2: normalized ticker ─────────────────────────────────────────────
    if raw_ticker:
        norm = normalize_ticker(raw_ticker)
        all_insts = session.execute(select(Instrument)).scalars().all()
        for inst in all_insts:
            if inst.ticker and normalize_ticker(inst.ticker) == norm:
                _add_alias(inst, raw_ticker)
                _write_decision(
                    session, batch_id, raw_ticker, inst.ticker, "normalized", Decimal("1")
                )
                return ResolveResult(
                    instrument_id=inst.id, method="normalized", confidence=Decimal("1")
                )

    # ── Step 3: alias table ───────────────────────────────────────────────────
    if raw_ticker:
        canonical_key = SWEEP_ALIASES.get(raw_ticker.upper())
        if canonical_key is not None:
            cash = get_or_create_cash(session)
            _add_alias(cash, raw_ticker)
            _write_decision(session, batch_id, raw_ticker, cash.ticker, "alias", Decimal("1"))
            return ResolveResult(
                instrument_id=cash.id, method="alias", confidence=Decimal("1")
            )

    # ── Step 4: name fuzzy ────────────────────────────────────────────────────
    if raw_name:
        all_insts = session.execute(select(Instrument)).scalars().all()
        best_score = 0
        candidates: list[tuple[Instrument, int]] = []
        for inst in all_insts:
            if not inst.name:
                continue
            score = fuzz.token_sort_ratio(raw_name.lower(), inst.name.lower())
            if score >= AUTO_THRESHOLD:
                # Auto-accept on the highest score; keep scanning for the best
                if score > best_score:
                    best_score = score
                    candidates = [(inst, score)]
                elif score == best_score:
                    candidates.append((inst, score))
            elif score >= QUEUE_THRESHOLD:
                candidates.append((inst, score))

        if candidates and candidates[0][1] >= AUTO_THRESHOLD:
            winner, score = candidates[0]
            _add_alias(winner, raw_name)
            _write_decision(
                session,
                batch_id,
                raw_name,
                winner.name,
                "name_fuzzy_auto",
                Decimal(score) / Decimal(100),
            )
            return ResolveResult(
                instrument_id=winner.id,
                method="name_fuzzy_auto",
                confidence=Decimal(score) / Decimal(100),
            )

        queue_candidates = [(i.id, s) for i, s in candidates if s >= QUEUE_THRESHOLD]
        if queue_candidates:
            _write_decision(
                session, batch_id, raw_name, None, "name_fuzzy_queue", Decimal("0")
            )
            _enqueue(session, raw_label, batch_id, queue_candidates)
            return ResolveResult(
                instrument_id=None,
                method="name_fuzzy_queue",
                confidence=Decimal("0"),
                queued=True,
                candidates=queue_candidates,
            )

        # <80: bare queue — fall through to step 5/6

    # ── Step 5: OpenFIGI ──────────────────────────────────────────────────────
    if raw_ticker and figi_lookup is not None:
        result = figi_lookup(raw_ticker)
        if result is not None:
            inst = _create_from_figi(session, result, raw_ticker)
            _write_decision(
                session, batch_id, raw_ticker, inst.ticker, "figi", Decimal("1")
            )
            return ResolveResult(
                instrument_id=inst.id, method="figi", confidence=Decimal("1")
            )

    # ── Step 6: bare queue for human confirm ──────────────────────────────────
    _write_decision(session, batch_id, raw_label, None, "unresolved", Decimal("0"))
    _enqueue(session, raw_label, batch_id, [])
    return ResolveResult(
        instrument_id=None,
        method="unresolved",
        confidence=Decimal("0"),
        queued=True,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_decision(
    session: Session,
    batch_id: int | None,
    raw_value: str,
    resolved_to: str | None,
    method: str,
    confidence: Decimal,
    accepted_by: str = "auto",
) -> None:
    session.add(
        MappingDecision(
            batch_id=batch_id,
            raw_value=raw_value,
            resolved_to=resolved_to,
            method=method,
            confidence=confidence,  # type: ignore[arg-type]
            accepted_by=accepted_by,
        )
    )


def _enqueue(
    session: Session,
    raw_value: str,
    batch_id: int | None,
    candidates: list[tuple[int, int]],
) -> None:
    """Add or update an entry in the unresolved queue."""
    existing = session.execute(
        select(UnresolvedQueue).where(
            UnresolvedQueue.queue_type == "instrument",
            UnresolvedQueue.raw_value == raw_value,
            UnresolvedQueue.resolved_at.is_(None),
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            UnresolvedQueue(
                queue_type="instrument",
                raw_value=raw_value,
                context_json={"batch_id": batch_id, "candidates": candidates},
            )
        )


def _add_alias(inst: Instrument, raw_value: str) -> None:
    """Add raw_value to inst.aliases if not already present."""
    aliases: list[str] = list(inst.aliases or [])
    if raw_value not in aliases and raw_value != inst.ticker:
        aliases.append(raw_value)
        inst.aliases = aliases
        flag_modified(inst, "aliases")


def _create_from_figi(session: Session, result: FigiResult, raw_ticker: str) -> Instrument:
    """Create and flush a new Instrument from an OpenFIGI result."""
    instrument_type = classify_from_figi_type(result.security_type) or classify_from_name(
        result.name
    )
    asset_class = infer_asset_class(instrument_type, result.name)
    inst = Instrument(
        ticker=result.ticker or raw_ticker,
        figi=result.figi or None,
        name=result.name or None,
        instrument_type=instrument_type,
        asset_class=asset_class,
        sleeve="unclassified",
        is_cash_equivalent=(instrument_type == "cash"),
        needs_unwind=needs_unwind_flag(instrument_type),
        aliases=[raw_ticker] if raw_ticker != (result.ticker or raw_ticker) else [],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst
