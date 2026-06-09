"""Provider payload archive — immutable, append-only raw response storage.

Every provider fetch must persist raw payload before normalization (FR-2.2).
Provider revisions create new records; existing records are never updated (FR-2.4).
Historical decision replay uses get_archived_payload(as_of=decision_time) to recover
the exact payload in use at that time (FR-2.5).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from committee.models import ProviderPayload

_POLICY_VERSION = "v1"
_PARSER_VERSION = "1.0"


def _sha256(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def archive_payload(
    session: Session,
    provider_name: str,
    endpoint: str,
    payload: dict,
    retrieved_at: datetime,
    effective_date: date | None = None,
    parser_version: str = _PARSER_VERSION,
    normalization_policy_version: str = _POLICY_VERSION,
) -> ProviderPayload:
    """Archive a raw provider response. Always creates a new record."""
    record = ProviderPayload(
        provider_name=provider_name,
        endpoint=endpoint,
        retrieved_at=retrieved_at,
        effective_date=effective_date,
        payload_hash=_sha256(payload),
        parser_version=parser_version,
        normalization_policy_version=normalization_policy_version,
        raw_payload=payload,
    )
    session.add(record)
    return record


def get_archived_payload(
    session: Session,
    provider_name: str,
    endpoint: str,
    as_of: datetime,
) -> ProviderPayload | None:
    """Retrieve the payload available at or before as_of (for historical replay).

    Returns None if no payload exists — never fabricates a value (Invariant H).
    """
    return session.execute(
        select(ProviderPayload)
        .where(
            ProviderPayload.provider_name == provider_name,
            ProviderPayload.endpoint == endpoint,
            ProviderPayload.retrieved_at <= as_of,
        )
        .order_by(ProviderPayload.retrieved_at.desc())
        .limit(1)
    ).scalar_one_or_none()
