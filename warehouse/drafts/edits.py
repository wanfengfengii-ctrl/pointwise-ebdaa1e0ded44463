from collections.abc import Sequence
from dataclasses import dataclass, replace
from sqlite3 import Connection

from warehouse.drafts.order import insert_row, move_row, remove_row, row_index
from warehouse.drafts.store import DraftRow, WorkingDay, load_draft, replace_draft
from warehouse.intake.observations import Origin, SourceObservation
from warehouse.intake.source_store import require_batch
from warehouse.intake.validation import validate_name, validate_reading


@dataclass(frozen=True, slots=True)
class Amendment:
    operation: str
    position: int
    argument: int | Origin | None = None


def _received_row(db: Connection, day: str, origin: Origin) -> DraftRow:
    if not isinstance(origin, Origin):
        raise ValueError('insert requires a received source origin')
    validate_name(origin.batch_id, 'source batch ID')
    batch = require_batch(db, origin.batch_id)
    original_origin = batch.origin_at(origin.line_no)
    if batch.day != day or original_origin != origin:
        raise ValueError('insert origin must identify a received row of the draft day')
    return DraftRow(batch.rows[origin.line_no - 1], original_origin)


def _apply(db: Connection, day: str, rows: list[DraftRow], edit: Amendment) -> None:
    if not isinstance(edit, Amendment):
        raise ValueError('edits must be Amendment instances')
    match edit.operation:
        case 'correct':
            index = row_index(edit.position, len(rows))
            prior = rows[index]
            observation = SourceObservation(prior.observation.label, validate_reading(edit.argument))
            rows[index] = replace(prior, observation=observation)
        case 'move':
            move_row(rows, edit.position, edit.argument)
        case 'remove':
            if edit.argument is not None:
                raise ValueError('remove takes no argument')
            remove_row(rows, edit.position)
        case 'insert':
            insert_row(rows, edit.position, _received_row(db, day, edit.argument))
        case _:
            raise ValueError(f'unknown amendment operation {edit.operation!r}')


def edit_draft(db: Connection, draft_id: int, edits: Sequence[Amendment]) -> WorkingDay:
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes, bytearray)):
        raise ValueError('edits must be an ordered sequence')
    operations = tuple(edits)
    db.execute('BEGIN IMMEDIATE')
    with db:
        draft = load_draft(db, draft_id)
        rows = list(draft.rows)
        for edit in operations:
            _apply(db, draft.base.day, rows, edit)
        updated = replace(draft, rows=tuple(rows))
        replace_draft(db, updated)
        return updated
