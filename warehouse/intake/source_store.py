from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.intake.observations import Origin, SourceObservation, decode_reading, encode_reading
from warehouse.intake.receipts import BatchReceipt


@dataclass(frozen=True, slots=True)
class SourceBatch:
    batch_id: str
    station: str
    day: str
    rows: tuple[SourceObservation, ...]
    receipt: BatchReceipt

    def origin_at(self, line_no: int) -> Origin:
        if type(line_no) is not int or not 1 <= line_no <= len(self.rows):
            raise ValueError('source line is outside the received batch')
        return Origin(self.station, self.batch_id, line_no)


def fetch_batch(db: Connection, batch_id: str) -> SourceBatch | None:
    header = db.execute(
        'SELECT station, day, token FROM source_batches WHERE batch_id = ?', (batch_id,),
    ).fetchone()
    if header is None:
        return None
    station, day, token = header
    rows = tuple(
        SourceObservation(label, decode_reading(liters))
        for label, liters in db.execute(
            'SELECT label, liters FROM source_rows WHERE batch_id = ? ORDER BY position',
            (batch_id,),
        )
    )
    return SourceBatch(batch_id, station, day, rows, BatchReceipt(batch_id, len(rows), token))


def require_batch(db: Connection, batch_id: str) -> SourceBatch:
    batch = fetch_batch(db, batch_id)
    if batch is None:
        raise LookupError(f'unknown received batch {batch_id!r}')
    return batch


def store_batch(db: Connection, batch: SourceBatch) -> None:
    db.execute(
        'INSERT INTO source_batches (batch_id, station, day, token) VALUES (?, ?, ?, ?)',
        (batch.batch_id, batch.station, batch.day, batch.receipt.token),
    )
    db.executemany(
        'INSERT INTO source_rows (batch_id, position, label, liters) VALUES (?, ?, ?, ?)',
        (
            (batch.batch_id, position, row.label, encode_reading(row.liters))
            for position, row in enumerate(batch.rows, 1)
        ),
    )
