from collections.abc import Callable
from sqlite3 import Connection

from warehouse.delivery.eligibility import select_pending_rows
from warehouse.delivery.page_store import (
    SuccessfulPage, last_successful_page_no, load_successful_page, store_successful_page,
)
from warehouse.delivery.receipts import issue_page_receipt
from warehouse.delivery.sessions import load_session
from warehouse.intake.validation import validate_record_id
from warehouse.publication.current import get_current_edition_id
from warehouse.publication.editions import load_edition


def _replay(page: SuccessfulPage, page_size: int) -> SuccessfulPage:
    if page.page_size != page_size:
        raise ValueError('page size differs from the successful request')
    return page


def take_page(
    db: Connection,
    session_id: str,
    page_no: int,
    page_size: int,
    *,
    before_select: Callable[[], None] | None = None,
    before_commit: Callable[[], None] | None = None,
    after_commit: Callable[[], None] | None = None,
) -> SuccessfulPage:
    validate_record_id(page_no, 'page number')
    if type(page_size) is not int or page_size < 1:
        raise ValueError('page size must be a positive integer')
    session = load_session(db, session_id)
    prior = load_successful_page(db, session_id, page_no)
    if prior is not None:
        return _replay(prior, page_size)
    if before_select is not None:
        before_select()

    db.execute('BEGIN IMMEDIATE')
    with db:
        # A caller on another connection may have committed this page since the first lookup.
        prior = load_successful_page(db, session_id, page_no)
        if prior is not None:
            return _replay(prior, page_size)
        if page_no != last_successful_page_no(db, session_id) + 1:
            raise ValueError('new page number must immediately follow the last successful page')
        edition_id = get_current_edition_id(db, session.day)
        if edition_id is None:
            raise LookupError(f'no published edition for {session.day}')
        edition = load_edition(db, edition_id)
        rows = select_pending_rows(db, session_id, edition, page_size)
        page = SuccessfulPage(page_size, rows, issue_page_receipt(session_id, page_no, edition_id, rows))
        store_successful_page(db, page)
        if before_commit is not None:
            before_commit()
    if after_commit is not None:
        after_commit()
    return page
