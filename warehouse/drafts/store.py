from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.drafts.ancestry import DraftBase
from warehouse.intake.observations import Origin, SourceObservation, decode_reading, encode_reading
from warehouse.intake.validation import validate_record_id


@dataclass(frozen=True, slots=True)
class DraftRow:
    observation: SourceObservation
    origin: Origin
    parent_position: int | None = None


@dataclass(frozen=True, slots=True)
class WorkingDay:
    draft_id: int
    base: DraftBase
    rows: tuple[DraftRow, ...]


def load_draft(db: Connection, draft_id: int) -> WorkingDay:
    validate_record_id(draft_id, 'draft ID')
    header = db.execute(
        'SELECT day, parent_id FROM drafts WHERE draft_id = ?', (draft_id,),
    ).fetchone()
    if header is None:
        raise LookupError(f'unknown draft {draft_id}')
    day, parent_id = header
    batch_ids = tuple(
        batch_id for (batch_id,) in db.execute(
            'SELECT batch_id FROM draft_inputs WHERE draft_id = ? ORDER BY position', (draft_id,),
        )
    )
    rows = tuple(
        DraftRow(SourceObservation(label, decode_reading(liters)), Origin(station, batch, line), parent)
        for label, liters, station, batch, line, parent in db.execute(
            '''SELECT label, liters, station, source_batch, source_position, parent_position
               FROM draft_rows WHERE draft_id = ? ORDER BY position''',
            (draft_id,),
        )
    )
    return WorkingDay(draft_id, DraftBase(day, batch_ids, parent_id), rows)


def insert_draft(db: Connection, base: DraftBase, rows: tuple[DraftRow, ...]) -> WorkingDay:
    draft_id = db.execute(
        'INSERT INTO drafts (day, parent_id) VALUES (?, ?)', (base.day, base.parent_id),
    ).lastrowid
    db.executemany(
        'INSERT INTO draft_inputs (draft_id, position, batch_id) VALUES (?, ?, ?)',
        ((draft_id, position, batch_id) for position, batch_id in enumerate(base.batch_ids, 1)),
    )
    draft = WorkingDay(draft_id, base, rows)
    _store_rows(db, draft)
    return draft


def replace_draft(db: Connection, draft: WorkingDay) -> None:
    db.execute('DELETE FROM draft_rows WHERE draft_id = ?', (draft.draft_id,))
    _store_rows(db, draft)


def _store_rows(db: Connection, draft: WorkingDay) -> None:
    db.executemany(
        '''INSERT INTO draft_rows
           (draft_id, position, label, liters, station, source_batch, source_position, parent_position)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            (
                draft.draft_id, position, row.observation.label, encode_reading(row.observation.liters),
                row.origin.station, row.origin.batch_id, row.origin.line_no, row.parent_position,
            )
            for position, row in enumerate(draft.rows, 1)
        ),
    )
