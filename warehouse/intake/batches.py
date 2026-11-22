from collections.abc import Sequence
from sqlite3 import Connection

from warehouse.intake.observations import SourceObservation
from warehouse.intake.receipts import BatchReceipt, issue_batch_receipt
from warehouse.intake.source_store import SourceBatch, fetch_batch, store_batch
from warehouse.intake.validation import validate_batch_input


def receive(
    db: Connection, station: str, day: str, batch_id: str, rows: Sequence[SourceObservation],
) -> BatchReceipt:
    observations = validate_batch_input(station, day, batch_id, rows)
    db.execute('BEGIN IMMEDIATE')
    with db:
        received = fetch_batch(db, batch_id)
        if received is not None:
            if (received.station, received.day, received.rows) != (station, day, observations):
                raise ValueError(f'batch ID {batch_id!r} already identifies a different original')
            return received.receipt
        receipt = issue_batch_receipt(batch_id, len(observations))
        store_batch(db, SourceBatch(batch_id, station, day, observations, receipt))
        return receipt
