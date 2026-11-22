from sqlite3 import Connection

from warehouse.delivery.page_store import read_delivery_evidence
from warehouse.publication.continuity import read_edition_continuity
from warehouse.publication.editions import Edition, EditionRow, load_edition


def select_pending_rows(
    db: Connection, session_id: str, edition: Edition, page_size: int,
) -> tuple[EditionRow, ...]:
    evidence = read_delivery_evidence(db, session_id)
    cursor = edition
    tracked = {row.position: row.position for row in edition.rows}
    delivered: set[int] = set()

    # Walk backward from current positions. A row without a parent stops here and
    # can only be excluded by evidence from its own edition or a later one.
    while tracked and evidence:
        for position in evidence.pop(cursor.edition_id, ()):
            if position in tracked:
                delivered.add(tracked.pop(position))
        if not tracked or not evidence or cursor.parent_id is None:
            break
        links = read_edition_continuity(db, cursor.edition_id)
        tracked = {
            links[position]: original for position, original in tracked.items() if position in links
        }
        if tracked:
            cursor = load_edition(db, cursor.parent_id)

    pending = tuple(row for row in edition.rows if row.position not in delivered)
    return pending[:page_size]
