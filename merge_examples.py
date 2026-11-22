"""Prepare two single-draft histories for the merge examples in MERGE.md."""
import json
import sqlite3
from contextlib import closing

from tests.support import DAY, initialize, readings
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.drafts.store import load_draft
from warehouse.intake.batches import receive
from warehouse.intake.observations import SourceObservation
from warehouse.publication.current import get_current_edition_id
from warehouse.publication.publish import publish_day


def prepare(db):
    receive(db, "upstream", DAY, "ab", (SourceObservation("A", 7), SourceObservation("B", 11)))
    initial = draft_day(db, DAY, batch_ids=["ab"])
    parent = publish_day(db, initial, "first").edition_id
    moved_a, moved_b, removed_a = (draft_day(db, DAY, parent_id=parent) for _ in range(3))
    edit_draft(db, moved_a, [Amendment("move", 1, 2)])
    edit_draft(db, moved_b, [Amendment("move", 2, 1)])
    edit_draft(db, removed_a, [Amendment("remove", 1)])
    return parent, moved_a, moved_b, removed_a


def main():
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as db:
        initialize(db)
        parent, moved_a, moved_b, removed_a = prepare(db)
        drafts = [load_draft(db, item) for item in (moved_a, moved_b, removed_a)]
        rows = [readings(draft.rows) for draft in drafts]
        positions = [[row.parent_position for row in draft.rows] for draft in drafts]
        assert rows == [[("B", 11), ("A", 7)], [("B", 11), ("A", 7)], [("B", 11)]]
        assert positions == [[2, 1], [2, 1], [2]]
        assert get_current_edition_id(db, DAY) == parent
        print(json.dumps({"draft_rows": rows, "parent_positions": positions, "current": parent}))


if __name__ == "__main__":
    main()
