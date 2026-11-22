from collections.abc import Sequence
from sqlite3 import Connection

from warehouse.drafts.ancestry import DraftBase, validate_draft_base
from warehouse.drafts.store import DraftRow, insert_draft
from warehouse.intake.source_store import require_batch
from warehouse.intake.validation import validate_day, validate_name
from warehouse.publication.editions import load_edition


def draft_day(
    db: Connection, day: str, *, batch_ids: Sequence[str] = (), parent_id: int | None = None,
) -> int:
    validate_day(day)
    if not isinstance(batch_ids, Sequence) or isinstance(batch_ids, (str, bytes, bytearray)):
        raise ValueError('batch IDs must be an ordered sequence')
    base = DraftBase(day, tuple(batch_ids), parent_id)
    for batch_id in base.batch_ids:
        validate_name(batch_id, 'batch ID')
    validate_draft_base(base)

    db.execute('BEGIN IMMEDIATE')
    with db:
        if base.parent_id is None:
            batches = tuple(require_batch(db, batch_id) for batch_id in base.batch_ids)
            validate_draft_base(base, (batch.day for batch in batches))
            rows = tuple(
                DraftRow(observation, batch.origin_at(line_no))
                for batch in batches
                for line_no, observation in enumerate(batch.rows, 1)
            )
        else:
            parent = load_edition(db, base.parent_id)
            validate_draft_base(base, (parent.day,))
            rows = tuple(DraftRow(row.observation, row.origin, row.position) for row in parent.rows)
        return insert_draft(db, base, rows).draft_id
