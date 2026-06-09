"""Provider archive tests (AC-3, AC-4, FR-2)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

import pytest

from committee.market.archive import archive_payload, get_archived_payload
from committee.models import NormalizedFact, ProviderPayload


def _sha256(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def test_archive_payload_creates_record(db_session):
    payload_data = {"cik": "12345", "revenue": 1000000}
    record = archive_payload(
        session=db_session,
        provider_name="edgar",
        endpoint="https://data.sec.gov/api/xbrl/companyfacts/CIK0000012345.json",
        payload=payload_data,
        retrieved_at=datetime(2024, 1, 15, 12, 0, 0),
        effective_date=date(2023, 12, 31),
        parser_version="1.0",
        normalization_policy_version="v1",
    )
    db_session.flush()
    assert record.id is not None
    assert record.payload_hash == _sha256(payload_data)
    assert record.provider_name == "edgar"


def test_provider_revision_creates_new_record(db_session):
    """When provider revises data, a new record is created (not an update)."""
    original = {"revenue": 1000000}
    revised = {"revenue": 1100000}

    r1 = archive_payload(
        db_session, "edgar", "https://example.com",
        original, datetime(2024, 1, 15), date(2023, 12, 31), "1.0", "v1",
    )
    r2 = archive_payload(
        db_session, "edgar", "https://example.com",
        revised, datetime(2024, 2, 15), date(2023, 12, 31), "1.0", "v1",
    )
    db_session.flush()
    assert r1.id != r2.id
    assert r1.payload_hash != r2.payload_hash
    from sqlalchemy import select as sa_select
    count = db_session.execute(sa_select(ProviderPayload)).scalars().all()
    assert len(count) >= 2


def test_get_archived_payload_for_decision_date(db_session):
    """Historical replay retrieves the payload available at decision time (AC-3)."""
    p_jan = archive_payload(
        db_session, "edgar", "https://example.com",
        {"revenue": 1000000}, datetime(2024, 1, 15), date(2023, 12, 31), "1.0", "v1",
    )
    p_feb = archive_payload(
        db_session, "edgar", "https://example.com",
        {"revenue": 1100000}, datetime(2024, 2, 15), date(2023, 12, 31), "1.0", "v1",
    )
    db_session.flush()
    # Decision was made on Jan 20 — should retrieve Jan payload
    result = get_archived_payload(
        db_session, "edgar", "https://example.com",
        as_of=datetime(2024, 1, 20),
    )
    assert result is not None
    assert result.id == p_jan.id


def test_no_fabrication_on_missing_provider(db_session):
    """When no archived payload exists, returns None (not a fabricated value)."""
    result = get_archived_payload(
        db_session, "missing_provider", "https://nonexistent.com",
        as_of=datetime(2024, 1, 20),
    )
    assert result is None
