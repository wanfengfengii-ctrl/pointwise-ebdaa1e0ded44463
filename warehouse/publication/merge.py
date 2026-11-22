"""Confirm a stored merge preview into a new current edition and receipt."""
from dataclasses import dataclass
from sqlite3 import Connection
from uuid import uuid4

from warehouse.drafts.store import DraftRow, draft_revision, load_draft
from warehouse.intake.observations import Origin, SourceObservation, decode_reading
from warehouse.intake.validation import validate_name, validate_record_id
from warehouse.publication.current import advance_current_edition, get_current_edition_id
from warehouse.publication.continuity import store_edition_continuity
from warehouse.publication.editions import EditionRow, store_edition


@dataclass(frozen=True, slots=True)
class MergeReceipt:
    request_id: str
    preview_id: int
    edition_id: int
    token: str


@dataclass(frozen=True, slots=True)
class MergePreview:
    preview_id: int
    day: str
    parent_id: int
    left_id: int
    right_id: int
    left_revision: int
    right_revision: int
    rows: tuple[DraftRow, ...]


def load_merge_preview(db: Connection, preview_id: int) -> MergePreview:
    header = db.execute(
        '''SELECT day, parent_id, left_id, right_id, left_revision, right_revision
           FROM merge_previews WHERE preview_id = ?''',
        (preview_id,),
    ).fetchone()
    if header is None:
        raise LookupError(f'unknown merge preview {preview_id}')
    rows = tuple(
        DraftRow(
            SourceObservation(label, decode_reading(liters)),
            Origin(station, batch, line),
            parent_position,
        )
        for label, liters, station, batch, line, parent_position in db.execute(
            '''SELECT label, liters, station, source_batch, source_position, parent_position
               FROM merge_preview_rows WHERE preview_id = ? ORDER BY position''',
            (preview_id,),
        )
    )
    return MergePreview(preview_id, *header, rows)


def load_merge_receipt(db: Connection, request_id: str) -> MergeReceipt | None:
    row = db.execute(
        'SELECT request_id, preview_id, edition_id, token FROM merge_receipts WHERE request_id = ?',
        (request_id,),
    ).fetchone()
    return None if row is None else MergeReceipt(*row)


def _require_merge_capable(db: Connection, draft_id: int) -> None:
    if db.execute(
        'SELECT 1 FROM merge_capable_drafts WHERE draft_id = ?', (draft_id,),
    ).fetchone() is None:
        raise ValueError('draft was created before the merge feature and cannot be merged')


def _require_fresh(db: Connection, preview: MergePreview) -> None:
    for draft_id, expected_revision in (
        (preview.left_id, preview.left_revision),
        (preview.right_id, preview.right_revision),
    ):
        draft = load_draft(db, draft_id)
        if draft.base.parent_id != preview.parent_id:
            raise ValueError('preview drafts no longer share the previewed parent')
        _require_merge_capable(db, draft_id)
        if draft_revision(db, draft_id) != expected_revision:
            raise ValueError('preview is stale: one of its drafts has been edited')
    if get_current_edition_id(db, preview.day) != preview.parent_id:
        raise ValueError('preview is stale: its parent is no longer current')


def store_merge_receipt(db: Connection, receipt: MergeReceipt) -> None:
    db.execute(
        '''INSERT INTO merge_receipts (request_id, preview_id, edition_id, token)
           VALUES (?, ?, ?, ?)''',
        (receipt.request_id, receipt.preview_id, receipt.edition_id, receipt.token),
    )


def confirm_merge(
    db: Connection,
    preview_id: int,
    request_id: str,
    *,
    before_commit=None,
) -> MergeReceipt:
    validate_record_id(preview_id, 'preview ID')
    validate_name(request_id, 'merge request ID')
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = load_merge_receipt(db, request_id)
        if prior is not None:
            if prior.preview_id != preview_id:
                raise ValueError('merge request ID already identifies another preview')
            return prior

        preview = load_merge_preview(db, preview_id)
        _require_fresh(db, preview)

        rows = tuple(
            EditionRow(position, row.observation, row.origin)
            for position, row in enumerate(preview.rows, 1)
        )
        links = {
            position: row.parent_position
            for position, row in enumerate(preview.rows, 1)
            if row.parent_position is not None
        }
        edition = store_edition(db, preview.day, preview.parent_id, rows)
        store_edition_continuity(db, edition, links)
        advance_current_edition(db, preview.day, preview.parent_id, edition.edition_id)
        receipt = MergeReceipt(request_id, preview_id, edition.edition_id, uuid4().hex)
        store_merge_receipt(db, receipt)
        if before_commit is not None:
            before_commit()
        return receipt
