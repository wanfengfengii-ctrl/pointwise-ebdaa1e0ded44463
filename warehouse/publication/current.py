from sqlite3 import Connection


def get_current_edition_id(db: Connection, day: str) -> int | None:
    row = db.execute('SELECT edition_id FROM current_editions WHERE day = ?', (day,)).fetchone()
    return None if row is None else row[0]


def advance_current_edition(
    db: Connection, day: str, expected_parent: int | None, edition_id: int,
) -> None:
    if get_current_edition_id(db, day) != expected_parent:
        raise ValueError('draft base is stale for the current edition')
    db.execute(
        '''INSERT INTO current_editions (day, edition_id) VALUES (?, ?)
           ON CONFLICT(day) DO UPDATE SET edition_id = excluded.edition_id''',
        (day, edition_id),
    )
