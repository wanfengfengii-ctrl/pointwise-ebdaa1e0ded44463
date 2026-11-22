from tests.support import DAY, OTHER_DAY, WarehouseTest, readings
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.drafts.store import load_draft
from warehouse.intake.observations import Origin
from warehouse.publication.publish import publish_day
from warehouse.reconcile.daybook import read_day


class TestDrafts(WarehouseTest):
    def test_invalid_edit(self):
        self.admit('a', 'upstream', ('A', 7))
        parent_id = self.first_edition('a')
        draft_id = draft_day(self.db, DAY, parent_id=parent_id)
        original = load_draft(self.db, draft_id)
        with self.assertRaisesRegex(ValueError, 'position'):
            edit_draft(self.db, draft_id, [Amendment('correct', 1, 13), Amendment('remove', 99)])
        self.assertEqual(load_draft(self.db, draft_id), original)
        self.assertEqual(readings(read_day(self.db, parent_id).rows), [('A', 7)])
        edition = publish_day(self.db, draft_id, 'after-rejection').edition_id
        self.assertEqual(readings(read_day(self.db, edition).rows), [('A', 7)])

    def test_ordered_edits(self):
        self.admit('a', 'upstream', ('A', 7), ('B', 11), ('M', None))
        self.admit('extra', 'downstream', ('C', 5))
        parent_id = self.first_edition('a')
        draft_id = draft_day(self.db, DAY, parent_id=parent_id)
        changed = edit_draft(self.db, draft_id, [
            Amendment('insert', 2, Origin('downstream', 'extra', 1)),
            Amendment('move', 4, 1),
            Amendment('correct', 3, 0),
            Amendment('remove', 2),
        ])
        self.assertEqual(readings(changed.rows), [('M', None), ('C', 0), ('B', 11)])
        self.assertEqual([row.origin for row in changed.rows], [
            Origin('upstream', 'a', 3), Origin('downstream', 'extra', 1), Origin('upstream', 'a', 2),
        ])
        edition = publish_day(self.db, draft_id, 'ordered').edition_id
        self.assertEqual(readings(read_day(self.db, edition).rows), [('M', None), ('C', 0), ('B', 11)])
        self.assertEqual(readings(read_day(self.db, parent_id).rows), [('A', 7), ('B', 11), ('M', None)])

    def test_rejects_invalid_bases_and_origins(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('tomorrow', 'downstream', ('B', 9), day=OTHER_DAY)
        parent_id = self.first_edition('a')
        for arguments in ({}, {'batch_ids': ['a', 'a']}, {'batch_ids': ['tomorrow']},
                          {'batch_ids': ['a'], 'parent_id': parent_id}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    draft_day(self.db, DAY, **arguments)
        with self.assertRaisesRegex(ValueError, 'draft day'):
            draft_day(self.db, OTHER_DAY, parent_id=parent_id)
        with self.assertRaises(LookupError):
            draft_day(self.db, DAY, parent_id=999)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM drafts').fetchone(), (1,))
        draft_id = draft_day(self.db, DAY, parent_id=parent_id)
        original = load_draft(self.db, draft_id)
        invalid_edits = (
            Amendment('insert', 1, Origin('downstream', 'tomorrow', 1)),
            Amendment('insert', 1, Origin('downstream', 'a', 1)),
            Amendment('insert', 1, Origin('upstream', 'a', 2)),
            Amendment('move', 1, True),
            Amendment('correct', 1, True),
            Amendment('unknown', 1),
        )
        for edit in invalid_edits:
            with self.subTest(edit=edit):
                with self.assertRaises(ValueError):
                    edit_draft(self.db, draft_id, [edit])
                self.assertEqual(load_draft(self.db, draft_id), original)
