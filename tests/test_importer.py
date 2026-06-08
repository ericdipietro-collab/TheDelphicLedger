"""Golden-file tests for the full import pipeline.

Run with UPDATE_GOLDENS=1 to regenerate all goldens.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

from committee.ingest.importer import ImportResult, process_file
from committee.ingest.template import MappingTemplate, load_template

EXAMPLES = Path(__file__).parent.parent / "examples"
PROFILES = Path(__file__).parent.parent / "profiles"
GOLDENS = Path(__file__).parent / "goldens"

UPDATE = os.environ.get("UPDATE_GOLDENS") == "1"


def _serialise(result: ImportResult) -> dict:
    """Convert ImportResult to a stable, Decimal-safe dict for golden comparison."""
    rows: list[dict] = []
    if result.file_type == "positions":
        for p in result.positions:
            rows.append({
                "instrument": p.instrument,
                "name": p.name,
                "qty": str(p.qty) if p.qty is not None else None,
                "price": str(p.price) if p.price is not None else None,
                "cost_basis": str(p.cost_basis) if p.cost_basis is not None else None,
                "market_value": str(p.market_value) if p.market_value is not None else None,
                "as_of": p.as_of.isoformat() if p.as_of else None,
            })
    else:
        for t in result.transactions:
            rows.append({
                "instrument": t.instrument,
                "trade_date": t.trade_date.isoformat(),
                "raw_type": t.raw_type,
                "canonical_type": t.canonical_type,
                "qty": str(t.qty) if t.qty is not None else None,
                "price": str(t.price) if t.price is not None else None,
                "amount": str(t.amount) if t.amount is not None else None,
            })
    return {
        "file_type": result.file_type,
        "broker_fingerprint": result.broker_fingerprint,
        "template_name": result.template_name,
        "row_count": result.row_count,
        "queued_types": sorted(result.queued_types),
        "rows": rows,
    }


def _run_golden(fixture_name: str, template_fingerprint: str) -> None:
    fixture = EXAMPLES / fixture_name
    template = load_template(template_fingerprint, PROFILES)
    assert template is not None, f"Template not found for fingerprint: {template_fingerprint}"

    content = fixture.read_bytes()
    result = process_file(content, fixture_name, template_override=template, profiles_dir=PROFILES)

    golden_path = GOLDENS / f"{fixture.stem}.json"
    actual = _serialise(result)

    if UPDATE:
        GOLDENS.mkdir(exist_ok=True)
        golden_path.write_text(json.dumps(actual, indent=2))
        return

    assert golden_path.exists(), (
        f"Golden file missing: {golden_path}. Run with UPDATE_GOLDENS=1 to generate."
    )
    expected = json.loads(golden_path.read_text())
    assert actual == expected, f"Golden mismatch for {fixture_name}"


# We need the actual fingerprints from the fixture headers. Use named templates.

def _load_template_by_name(name: str) -> MappingTemplate:
    for path in PROFILES.glob("*.yaml"):
        import yaml
        data = yaml.safe_load(path.read_text())
        if data and data.get("name") == name:
            return MappingTemplate.model_validate(data)
    raise FileNotFoundError(f"Template '{name}' not found in {PROFILES}")


def _run_golden_by_name(fixture_name: str, template_name: str) -> None:
    fixture = EXAMPLES / fixture_name
    template = _load_template_by_name(template_name)

    content = fixture.read_bytes()
    result = process_file(content, fixture_name, template_override=template, profiles_dir=PROFILES)

    golden_path = GOLDENS / f"{fixture.stem}.json"
    actual = _serialise(result)

    if UPDATE:
        GOLDENS.mkdir(exist_ok=True)
        golden_path.write_text(json.dumps(actual, indent=2))
        return

    assert golden_path.exists(), (
        f"Golden file missing: {golden_path}. Run with UPDATE_GOLDENS=1 to generate."
    )
    expected = json.loads(golden_path.read_text())
    assert actual == expected, f"Golden mismatch for {fixture_name}"


def test_schwab_positions() -> None:
    _run_golden_by_name("schwab_positions.csv", "Schwab Positions")


def test_schwab_transactions() -> None:
    _run_golden_by_name("schwab_transactions.csv", "Schwab Transactions")


def test_fidelity_positions() -> None:
    _run_golden_by_name("fidelity_positions.csv", "Fidelity Positions")


def test_fidelity_transactions() -> None:
    _run_golden_by_name("fidelity_transactions.csv", "Fidelity Transactions")


def test_vanguard_positions() -> None:
    _run_golden_by_name("vanguard_positions.csv", "Vanguard Positions")


def test_vanguard_transactions() -> None:
    _run_golden_by_name("vanguard_transactions.csv", "Vanguard Transactions")


# --------------------------------------------------------------------------
# Invariant checks (no golden needed)
# --------------------------------------------------------------------------

def test_no_float_in_position_rows() -> None:
    """Invariant E: all parsed numeric values must be Decimal, never float."""
    content = (EXAMPLES / "schwab_positions.csv").read_bytes()
    template = _load_template_by_name("Schwab Positions")
    result = process_file(content, "schwab_positions.csv", template_override=template)
    for pos in result.positions:
        for field in ("qty", "price", "cost_basis", "market_value"):
            val = getattr(pos, field)
            if val is not None:
                assert isinstance(val, Decimal), f"{field} is {type(val)}, expected Decimal"


def test_no_float_in_transaction_rows() -> None:
    content = (EXAMPLES / "schwab_transactions.csv").read_bytes()
    template = _load_template_by_name("Schwab Transactions")
    result = process_file(content, "schwab_transactions.csv", template_override=template)
    for txn in result.transactions:
        for field in ("qty", "price", "amount", "fees"):
            val = getattr(txn, field)
            if val is not None:
                assert isinstance(val, Decimal), f"{field} is {type(val)}, expected Decimal"


def test_schwab_transfer_queued() -> None:
    """'Transfer of securities in' must not auto-canonicalize — it goes to the queue."""
    content = (EXAMPLES / "schwab_transactions.csv").read_bytes()
    template = _load_template_by_name("Schwab Transactions")
    result = process_file(content, "schwab_transactions.csv", template_override=template)
    # The template has no alias for "Transfer of securities in"
    transfer_rows = [t for t in result.transactions if "transfer of securities" in t.raw_type.lower()]
    assert len(transfer_rows) == 1
    assert transfer_rows[0].canonical_type is None
    assert "Transfer of securities in" in result.queued_types


def test_spaxx_ticker_cleaned() -> None:
    """'SPAXX **' should parse to 'SPAXX' (asterisks stripped)."""
    content = (EXAMPLES / "schwab_positions.csv").read_bytes()
    template = _load_template_by_name("Schwab Positions")
    result = process_file(content, "schwab_positions.csv", template_override=template)
    tickers = [p.instrument for p in result.positions]
    assert "SPAXX" in tickers
    assert not any("*" in t for t in tickers)


def test_brk_slash_preserved() -> None:
    """BRK/B should be preserved as-is (no normalization in M1)."""
    content = (EXAMPLES / "schwab_positions.csv").read_bytes()
    template = _load_template_by_name("Schwab Positions")
    result = process_file(content, "schwab_positions.csv", template_override=template)
    tickers = [p.instrument for p in result.positions]
    assert "BRK/B" in tickers


def test_fidelity_brk_dot() -> None:
    """Fidelity uses BRK.B — preserved as-is in M1."""
    content = (EXAMPLES / "fidelity_positions.csv").read_bytes()
    template = _load_template_by_name("Fidelity Positions")
    result = process_file(content, "fidelity_positions.csv", template_override=template)
    tickers = [p.instrument for p in result.positions]
    assert "BRK.B" in tickers


def test_duplicate_hash_detection() -> None:
    """Same file content processed twice should produce identical file_hash."""
    content = (EXAMPLES / "schwab_positions.csv").read_bytes()
    template = _load_template_by_name("Schwab Positions")
    r1 = process_file(content, "schwab_positions.csv", template_override=template)
    r2 = process_file(content, "copy_of_schwab.csv", template_override=template)
    assert r1.file_hash == r2.file_hash


def test_vanguard_positions_no_ticker_fund() -> None:
    """Vanguard VMFXX has a ticker; all rows should parse without crashing."""
    content = (EXAMPLES / "vanguard_positions.csv").read_bytes()
    template = _load_template_by_name("Vanguard Positions")
    result = process_file(content, "vanguard_positions.csv", template_override=template)
    assert result.row_count >= 5


def test_fidelity_you_bought_canonicalizes() -> None:
    content = (EXAMPLES / "fidelity_transactions.csv").read_bytes()
    template = _load_template_by_name("Fidelity Transactions")
    result = process_file(content, "fidelity_transactions.csv", template_override=template)
    buy_rows = [t for t in result.transactions if "YOU BOUGHT" in t.raw_type.upper()]
    assert len(buy_rows) >= 1
    assert all(r.canonical_type == "buy" for r in buy_rows)
