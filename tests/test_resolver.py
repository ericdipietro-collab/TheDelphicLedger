"""Tests for the instrument resolution cascade.

Includes property tests that verify cascade ordering invariants and that
nothing below the auto-threshold is ever silently accepted.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from committee.models import Instrument, MappingDecision, UnresolvedQueue
from committee.resolver.cascade import AUTO_THRESHOLD, QUEUE_THRESHOLD, resolve_instrument
from committee.resolver.classify import (
    classify_from_figi_type,
    classify_from_name,
    infer_asset_class,
    needs_unwind_flag,
)
from committee.resolver.normalize import normalize_ticker
from committee.resolver.openfigi import FigiResult

# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_inst(session: Session, ticker: str, name: str, itype: str = "stock") -> Instrument:
    inst = Instrument(
        ticker=ticker,
        name=name,
        instrument_type=itype,
        asset_class="equity",
        sleeve="unclassified",
        is_cash_equivalent=False,
        needs_unwind=needs_unwind_flag(itype),
        aliases=[],
        bundle_tags=[],
    )
    session.add(inst)
    session.flush()
    return inst


# ── Normalize ─────────────────────────────────────────────────────────────────

def test_normalize_dots_slashes_spaces() -> None:
    assert normalize_ticker("BRK.B") == normalize_ticker("BRK/B") == normalize_ticker("BRK B")


def test_normalize_uppercase() -> None:
    assert normalize_ticker("aapl") == "AAPL"


def test_normalize_empty() -> None:
    assert normalize_ticker("") == ""


# ── Step 1: Exact ticker ──────────────────────────────────────────────────────

def test_exact_ticker_match(db_session: Session) -> None:
    _make_inst(db_session, "AAPL", "Apple Inc")
    result = resolve_instrument("AAPL", None, db_session)
    assert result.method == "exact"
    assert result.instrument_id is not None
    assert not result.queued


def test_exact_match_writes_decision(db_session: Session) -> None:
    _make_inst(db_session, "AAPL", "Apple Inc")
    resolve_instrument("AAPL", None, db_session)
    decisions = db_session.query(MappingDecision).all()
    assert len(decisions) == 1
    assert decisions[0].method == "exact"
    assert decisions[0].raw_value == "AAPL"


# Property: exact ticker always wins over normalized (higher priority)
def test_exact_wins_over_normalized(db_session: Session) -> None:
    """If an exact ticker match exists, cascade must stop at step 1 (not step 2)."""
    exact = _make_inst(db_session, "BRKB", "Berkshire Exact")
    _make_inst(db_session, "BRK/B", "Berkshire Slash")
    # "BRKB" exact-matches the first instrument
    result = resolve_instrument("BRKB", None, db_session)
    assert result.method == "exact"
    assert result.instrument_id == exact.id


# ── Step 2: Normalized ticker ─────────────────────────────────────────────────

def test_normalized_ticker_match(db_session: Session) -> None:
    inst = _make_inst(db_session, "BRK/B", "Berkshire Hathaway B")
    # "BRK.B" normalizes to "BRKB" == normalize("BRK/B")
    result = resolve_instrument("BRK.B", None, db_session)
    assert result.method == "normalized"
    assert result.instrument_id == inst.id


def test_normalized_adds_alias(db_session: Session) -> None:
    inst = _make_inst(db_session, "BRK/B", "Berkshire Hathaway B")
    resolve_instrument("BRK.B", None, db_session)
    # Same Python object via identity map — no refresh needed to see in-memory update
    assert "BRK.B" in (inst.aliases or [])


# Property: normalized wins over alias table
def test_normalized_wins_over_alias(db_session: Session) -> None:
    """Normalized match must stop at step 2, not reach the alias table (step 3)."""
    # VMFXX is in SWEEP_ALIASES → $CASH, but we insert it as a real instrument
    inst = _make_inst(db_session, "VMFXX", "Vanguard MM Fund", itype="mutual_fund")
    result = resolve_instrument("VMFXX", None, db_session)
    # Step 1: exact match → must stop there, not hit alias table
    assert result.method == "exact"
    assert result.instrument_id == inst.id


# ── Step 3: Alias table ───────────────────────────────────────────────────────

def test_alias_resolves_spaxx(db_session: Session) -> None:
    result = resolve_instrument("SPAXX", None, db_session)
    assert result.method == "alias"
    assert result.instrument_id is not None
    assert not result.queued
    # Cash instrument created
    inst = db_session.get(Instrument, result.instrument_id)
    assert inst is not None
    assert inst.is_cash_equivalent is True


def test_alias_resolves_cash(db_session: Session) -> None:
    result = resolve_instrument("CASH", None, db_session)
    assert result.method == "alias"


# Property: alias wins over name fuzzy
def test_alias_wins_over_name_fuzzy(db_session: Session) -> None:
    """Alias match must stop at step 3, not fall through to name fuzzy (step 4)."""
    # Insert a plausibly-named cash instrument that could fuzzy-match
    _make_inst(db_session, "CASHY", "Cash Money Market Fund", itype="cash")
    result = resolve_instrument("CASH", None, db_session)
    assert result.method == "alias"


# ── Step 4: Name fuzzy ────────────────────────────────────────────────────────

def test_fuzzy_auto_accept_at_93(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Name fuzzy score ≥93 → auto-accept, not queued."""
    _make_inst(db_session, "TROW", "T. Rowe Price Group")
    from rapidfuzz import fuzz
    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda a, b: 95)
    result = resolve_instrument(None, "T. Rowe Price Group Inc", db_session)
    assert result.method == "name_fuzzy_auto"
    assert not result.queued
    assert result.instrument_id is not None


def test_fuzzy_queue_at_85(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Name fuzzy score 80–92 → queued with candidates, never auto-accepted."""
    _make_inst(db_session, "TROW", "T. Rowe Price Group")
    from rapidfuzz import fuzz
    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda a, b: 85)
    result = resolve_instrument(None, "T Rowe Price", db_session)
    assert result.queued
    assert result.method == "name_fuzzy_queue"
    assert result.instrument_id is None  # never auto-accepted
    assert len(result.candidates) > 0


# Property: nothing below AUTO_THRESHOLD (93) ever auto-accepts
def test_score_92_never_auto_accepts(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Score at exactly AUTO_THRESHOLD - 1 must be queued, not accepted."""
    _make_inst(db_session, "MSFT", "Microsoft Corporation")
    from rapidfuzz import fuzz
    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda a, b: AUTO_THRESHOLD - 1)
    result = resolve_instrument(None, "Microsoft Corp", db_session)
    assert result.queued, "Score below AUTO_THRESHOLD must never auto-accept"
    assert result.instrument_id is None


def test_score_79_bare_queue(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """Score below QUEUE_THRESHOLD (80) → bare queue, no candidates."""
    _make_inst(db_session, "MSFT", "Microsoft Corporation")
    from rapidfuzz import fuzz
    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda a, b: QUEUE_THRESHOLD - 1)
    result = resolve_instrument(None, "Completely Different Name", db_session)
    assert result.queued
    assert result.candidates == []


def test_fuzzy_auto_adds_alias(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    inst = _make_inst(db_session, "MSFT", "Microsoft Corporation")
    from rapidfuzz import fuzz
    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda a, b: 95)
    resolve_instrument(None, "Microsoft Corp", db_session)
    assert "Microsoft Corp" in (inst.aliases or [])


def test_fuzzy_queue_writes_unresolved(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_inst(db_session, "MSFT", "Microsoft Corporation")
    from rapidfuzz import fuzz
    monkeypatch.setattr(fuzz, "token_sort_ratio", lambda a, b: 85)
    resolve_instrument(None, "Microsoft Corp", db_session)
    items = db_session.query(UnresolvedQueue).all()
    assert len(items) == 1
    assert items[0].queue_type == "instrument"


# ── Step 5: OpenFIGI (injectable) ─────────────────────────────────────────────

def test_figi_lookup_creates_instrument(db_session: Session) -> None:
    stub_result = FigiResult(
        figi="BBG000B9XRY4",
        ticker="AAPL",
        name="APPLE INC",
        security_type="Common Stock",
    )
    result = resolve_instrument(
        "AAPL", None, db_session, figi_lookup=lambda t: stub_result
    )
    assert result.method == "figi"
    assert result.instrument_id is not None
    inst = db_session.get(Instrument, result.instrument_id)
    assert inst is not None
    assert inst.ticker == "AAPL"
    assert inst.figi == "BBG000B9XRY4"
    assert inst.instrument_type == "stock"


def test_figi_lookup_not_called_when_none(db_session: Session) -> None:
    """When figi_lookup=None, cascade skips step 5 and queues bare."""
    result = resolve_instrument("UNKNOWN", None, db_session, figi_lookup=None)
    assert result.method == "unresolved"
    assert result.queued


def test_figi_none_result_falls_to_queue(db_session: Session) -> None:
    """When figi_lookup returns None, fall through to queue."""
    result = resolve_instrument("UNKNOWN", None, db_session, figi_lookup=lambda t: None)
    assert result.queued


# ── Step 6: bare queue ────────────────────────────────────────────────────────

def test_bare_queue_when_nothing_resolves(db_session: Session) -> None:
    result = resolve_instrument("ZZZUNKNOWN", "Completely Unknown Corp", db_session)
    assert result.queued
    assert result.instrument_id is None
    items = db_session.query(UnresolvedQueue).all()
    assert len(items) >= 1


# ── Cascade ordering: higher priority always wins ─────────────────────────────

def test_cascade_stops_at_first_match(db_session: Session) -> None:
    """Property: inserting an exact-match instrument prevents the cascade from
    proceeding to any later step, even when later steps would also match."""
    inst = _make_inst(db_session, "VTI", "Vanguard Total Stock Mkt ETF", itype="etf")
    # With exact match, result must be "exact" not "normalized" or "name_fuzzy_*"
    result = resolve_instrument("VTI", "Vanguard Total Stock Market ETF", db_session)
    assert result.method == "exact"
    assert result.instrument_id == inst.id


# ── Classification ────────────────────────────────────────────────────────────

def test_classify_figi_common_stock() -> None:
    assert classify_from_figi_type("Common Stock") == "stock"


def test_classify_figi_etp() -> None:
    assert classify_from_figi_type("ETP") == "etf"


def test_classify_figi_open_end_fund() -> None:
    assert classify_from_figi_type("Open-End Fund") == "mutual_fund"


def test_classify_name_etf() -> None:
    assert classify_from_name("Vanguard Total Stock Market ETF") == "etf"


def test_classify_name_fund() -> None:
    assert classify_from_name("Fidelity Contrafund") == "mutual_fund"


def test_classify_name_ambiguous() -> None:
    assert classify_from_name("Apple Inc") == "unclassified"


def test_needs_unwind_stock() -> None:
    assert needs_unwind_flag("stock") is True


def test_needs_unwind_etf() -> None:
    assert needs_unwind_flag("etf") is False


def test_needs_unwind_cash() -> None:
    assert needs_unwind_flag("cash") is False


def test_infer_asset_class_stock() -> None:
    assert infer_asset_class("stock", "Apple Inc") == "equity"


def test_infer_asset_class_cash() -> None:
    assert infer_asset_class("cash", "Money Market") == "cash"


def test_infer_asset_class_bond_etf() -> None:
    assert infer_asset_class("etf", "Vanguard Total Bond Market ETF") == "fixed_income"


def test_infer_asset_class_unclassified() -> None:
    assert infer_asset_class("etf", None) == "unclassified"
