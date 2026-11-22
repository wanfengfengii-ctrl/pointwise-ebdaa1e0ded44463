from collections.abc import Sequence
from dataclasses import dataclass, replace
from sqlite3 import Connection

from warehouse.drafts.order import insert_row, move_row, remove_row, row_index
from warehouse.drafts.store import (
    DraftRow, WorkingDay, bump_draft_revision, load_draft, note_moved_originals, replace_draft,
)
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


def _apply(db: Connection, day: str, rows: list[DraftRow], edit: Amendment) -> int | None:
    if not isinstance(edit, Amendment):
        raise ValueError('edits must be Amendment instances')
    match edit.operation:
        case 'correct':
            index = row_index(edit.position, len(rows))
            prior = rows[index]
            observation = SourceObservation(prior.observation.label, validate_reading(edit.argument))
            rows[index] = replace(prior, observation=observation)
        case 'move':
            source = row_index(edit.position, len(rows))
            target = row_index(edit.argument, len(rows))
            prior = rows[source]
            move_row(rows, edit.position, edit.argument)
            # An in-place move is not a relocation, so it never counts as an
            # explicit move for conflict or order purposes.
            if source != target and prior.parent_position is not None:
                return prior.parent_position
        case 'remove':
            if edit.argument is not None:
                raise ValueError('remove takes no argument')
            remove_row(rows, edit.position)
        case 'insert':
            insert_row(rows, edit.position, _received_row(db, day, edit.argument))
        case _:
            raise ValueError(f'unknown amendment operation {edit.operation!r}')
    return None


def edit_draft(db: Connection, draft_id: int, edits: Sequence[Amendment]) -> WorkingDay:
    if not isinstance(edits, Sequence) or isinstance(edits, (str, bytes, bytearray)):
        raise ValueError('edits must be an ordered sequence')
    operations = tuple(edits)
    db.execute('BEGIN IMMEDIATE')
    with db:
        draft = load_draft(db, draft_id)
        rows = list(draft.rows)
        moved: list[int] = []
        for edit in operations:
            relocated = _apply(db, draft.base.day, rows, edit)
            if relocated is not None:
                moved.append(relocated)
        updated = replace(draft, rows=tuple(rows))
        replace_draft(db, updated)
        if operations:
            note_moved_originals(db, draft_id, moved)
            bump_draft_revision(db, draft_id)
        return updated
