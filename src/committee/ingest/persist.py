"""Persist an ImportResult to the database."""

from __future__ import annotations

from sqlalchemy.orm import Session

from committee.ingest.importer import ImportResult, PositionRow, TransactionRow
from committee.models import (
    ImportBatch,
    MappingDecision,
    PositionSnapshot,
    Transaction,
    UnresolvedQueue,
)


def persist_import(result: ImportResult, session: Session) -> ImportBatch:
    """Write an ImportResult to the DB. Returns the new ImportBatch.

    Raises ValueError if the file_hash already exists (no-op duplicate).
    """
    existing = session.query(ImportBatch).filter_by(file_hash=result.file_hash).first()
    if existing is not None and existing.row_count > 0:
        raise ValueError(f"Duplicate file: hash {result.file_hash[:16]}… already imported")
    # existing with row_count == 0 was a void/failed import — allow re-import

    batch = ImportBatch(
        file_hash=result.file_hash,
        original_filename=result.filename,
        file_type=result.file_type,
        broker_fingerprint=result.broker_fingerprint,
        row_count=result.row_count,
        template_name=result.template_name,
    )
    session.add(batch)
    session.flush()  # get batch.id

    if result.file_type == "positions":
        for row in result.positions:
            _persist_position(row, batch, session)
    else:
        for row in result.transactions:
            _persist_transaction(row, batch, session)

    # Queue unknown transaction types
    for raw_type in result.queued_types:
        existing_q = (
            session.query(UnresolvedQueue)
            .filter_by(queue_type="txn_type", raw_value=raw_type, resolved_at=None)
            .first()
        )
        if existing_q is None:
            session.add(
                UnresolvedQueue(
                    queue_type="txn_type",
                    raw_value=raw_type,
                    context_json={"batch_id": batch.id},
                )
            )

    # Audit the column mapping decisions
    for src_col, canonical in result.column_map.items():
        session.add(
            MappingDecision(
                batch_id=batch.id,
                raw_value=src_col,
                resolved_to=canonical,
                method="column_map",
                confidence=None,
            )
        )

    return batch


def _persist_position(row: PositionRow, batch: ImportBatch, session: Session) -> None:
    session.add(
        PositionSnapshot(
            batch_id=batch.id,
            account_id=None,
            instrument_id=None,
            raw_instrument=row.instrument,
            as_of=row.as_of,
            qty=row.qty,
            price=row.price,
            market_value=row.market_value,
            cost_basis=row.cost_basis,
        )
    )


def _persist_transaction(row: TransactionRow, batch: ImportBatch, session: Session) -> None:
    session.add(
        Transaction(
            batch_id=batch.id,
            account_id=None,
            instrument_id=None,
            raw_instrument=row.instrument,
            trade_date=row.trade_date,
            settle_date=row.settle_date,
            raw_type=row.raw_type,
            canonical_type=row.canonical_type,
            qty=row.qty,
            price=row.price,
            amount=row.amount,
            fees=row.fees,
        )
    )
