from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.intake.observations import Origin, SourceObservation, decode_reading, encode_reading
from warehouse.intake.validation import validate_record_id


@dataclass(frozen=True, slots=True)
class EditionRow:
    position: int
    observation: SourceObservation
    origin: Origin


@dataclass(frozen=True, slots=True)
class Edition:
    edition_id: int
    day: str
    parent_id: int | None
    rows: tuple[EditionRow, ...]


def load_edition(db: Connection, edition_id: int) -> Edition:
    validate_record_id(edition_id, 'edition ID')
    header = db.execute(
        'SELECT day, parent_id FROM editions WHERE edition_id = ?', (edition_id,),
    ).fetchone()
    if header is None:
        raise LookupError(f'unknown edition {edition_id}')
    rows = tuple(
        EditionRow(position, SourceObservation(label, decode_reading(liters)), Origin(station, batch, line))
        for position, label, liters, station, batch, line in db.execute(
            '''SELECT position, label, liters, station, source_batch, source_position
               FROM edition_rows WHERE edition_id = ? ORDER BY position''',
            (edition_id,),
        )
    )
    day, parent_id = header
    return Edition(edition_id, day, parent_id, rows)


def store_edition(
    db: Connection, day: str, parent_id: int | None, rows: tuple[EditionRow, ...],
) -> Edition:
    edition_id = db.execute(
        'INSERT INTO editions (day, parent_id) VALUES (?, ?)', (day, parent_id),
    ).lastrowid
    db.executemany(
        '''INSERT INTO edition_rows
           (edition_id, position, label, liters, station, source_batch, source_position)
           VALUES (?, ?, ?, ?, ?, ?, ?)''',
        (
            (
                edition_id, row.position, row.observation.label, encode_reading(row.observation.liters),
                row.origin.station, row.origin.batch_id, row.origin.line_no,
            )
            for row in rows
        ),
    )
    return Edition(edition_id, day, parent_id, rows)
