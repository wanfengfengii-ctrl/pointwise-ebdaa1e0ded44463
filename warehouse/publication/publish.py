from collections.abc import Callable
from sqlite3 import Connection

from warehouse.drafts.store import load_draft
from warehouse.intake.validation import validate_name, validate_record_id
from warehouse.publication.continuity import derive_continuity, store_edition_continuity
from warehouse.publication.current import advance_current_edition
from warehouse.publication.editions import EditionRow, load_edition, store_edition
from warehouse.publication.receipts import (
    PublicationReceipt, issue_publication_receipt, load_publication_receipt, store_publication_receipt,
)


def publish_day(
    db: Connection, draft_id: int, request_id: str, *, before_commit: Callable[[], None] | None = None,
) -> PublicationReceipt:
    validate_record_id(draft_id, 'draft ID')
    validate_name(request_id, 'publication request ID')
    db.execute('BEGIN IMMEDIATE')
    with db:
        prior = load_publication_receipt(db, request_id)
        if prior is not None:
            if prior.draft_id != draft_id:
                raise ValueError('publication request ID already identifies another draft')
            return prior

        draft = load_draft(db, draft_id)
        parent_id = draft.base.parent_id
        parent = None if parent_id is None else load_edition(db, parent_id)
        links = derive_continuity(draft, parent)
        rows = tuple(
            EditionRow(position, row.observation, row.origin)
            for position, row in enumerate(draft.rows, 1)
        )
        edition = store_edition(db, draft.base.day, parent_id, rows)
        store_edition_continuity(db, edition, links)
        advance_current_edition(db, edition.day, parent_id, edition.edition_id)
        receipt = issue_publication_receipt(request_id, draft_id, edition.edition_id)
        store_publication_receipt(db, receipt)
        if before_commit is not None:
            before_commit()
        return receipt
