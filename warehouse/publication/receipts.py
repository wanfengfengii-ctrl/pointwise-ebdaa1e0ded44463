from dataclasses import dataclass
from sqlite3 import Connection
from uuid import uuid4

from warehouse.intake.validation import validate_name, validate_record_id


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    request_id: str
    draft_id: int
    edition_id: int
    token: str


def issue_publication_receipt(request_id: str, draft_id: int, edition_id: int) -> PublicationReceipt:
    return PublicationReceipt(request_id, draft_id, edition_id, uuid4().hex)


def store_publication_receipt(db: Connection, receipt: PublicationReceipt) -> None:
    db.execute(
        '''INSERT INTO publication_receipts (request_id, draft_id, edition_id, token)
           VALUES (?, ?, ?, ?)''',
        (receipt.request_id, receipt.draft_id, receipt.edition_id, receipt.token),
    )


def load_publication_receipt(
    db: Connection, request_id: str | None = None, *, edition_id: int | None = None,
) -> PublicationReceipt | None:
    if (request_id is None) == (edition_id is None):
        raise ValueError('look up the receipt by request ID or edition ID')
    if request_id is not None:
        validate_name(request_id, 'publication request ID')
        row = db.execute(
            '''SELECT request_id, draft_id, edition_id, token FROM publication_receipts
               WHERE request_id = ?''',
            (request_id,),
        ).fetchone()
    else:
        validate_record_id(edition_id, 'edition ID')
        row = db.execute(
            '''SELECT request_id, draft_id, edition_id, token FROM publication_receipts
               WHERE edition_id = ?''',
            (edition_id,),
        ).fetchone()
    return None if row is None else PublicationReceipt(*row)
