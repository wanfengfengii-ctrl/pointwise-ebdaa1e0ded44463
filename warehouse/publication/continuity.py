from sqlite3 import Connection

from warehouse.drafts.store import WorkingDay
from warehouse.publication.editions import Edition


def derive_continuity(draft: WorkingDay, parent: Edition | None) -> dict[int, int]:
    if draft.base.parent_id != (None if parent is None else parent.edition_id):
        raise ValueError('continuity requires the fixed draft parent')
    parent_rows = {} if parent is None else {row.position: row for row in parent.rows}
    links: dict[int, int] = {}
    continued: set[int] = set()
    for position, row in enumerate(draft.rows, 1):
        previous_position = row.parent_position
        if previous_position is None:
            continue
        previous = parent_rows.get(previous_position)
        if previous is None or previous_position in continued:
            raise ValueError('a continuing row must identify one distinct row of the parent')
        if previous.origin != row.origin or previous.observation.label != row.observation.label:
            raise ValueError('a continuing row must retain its original observation and provenance')
        continued.add(previous_position)
        links[position] = previous_position
    return links


def read_edition_continuity(db: Connection, edition_id: int) -> dict[int, int]:
    return dict(db.execute(
        'SELECT position, parent_position FROM edition_links WHERE edition_id = ?', (edition_id,),
    ))


def store_edition_continuity(db: Connection, edition: Edition, links: dict[int, int]) -> None:
    db.executemany(
        '''INSERT INTO edition_links (edition_id, position, parent_id, parent_position)
           VALUES (?, ?, ?, ?)''',
        (
            (edition.edition_id, position, edition.parent_id, parent_position)
            for position, parent_position in links.items()
        ),
    )
