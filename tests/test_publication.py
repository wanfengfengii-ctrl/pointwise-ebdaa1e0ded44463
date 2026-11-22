from tests.support import DAY, CommitInterrupted, WarehouseTest, interrupt_commit, readings
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.publication.current import get_current_edition_id
from warehouse.publication.publish import publish_day
from warehouse.publication.receipts import load_publication_receipt
from warehouse.reconcile.daybook import read_day


class TestPublication(WarehouseTest):
    def test_commit_failure(self):
        self.admit('a', 'upstream', ('A', 7))
        first = self.first_edition('a')
        draft = draft_day(self.db, DAY, parent_id=first)
        edit_draft(self.db, draft, [Amendment('correct', 1, 13)])
        with self.assertRaises(CommitInterrupted):
            publish_day(self.db, draft, 'amend', before_commit=interrupt_commit)
        self.assertEqual(get_current_edition_id(self.db, DAY), first)
        self.assertEqual(readings(read_day(self.db, first).rows), [('A', 7)])
        self.assertIsNone(load_publication_receipt(self.db, 'amend'))
        with self.assertRaises(LookupError):
            read_day(self.db, first + 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM edition_links').fetchone(), (0,))

        receipt = publish_day(self.db, draft, 'amend')
        self.assertEqual(get_current_edition_id(self.db, DAY), receipt.edition_id)
        self.assertEqual(readings(read_day(self.db, receipt.edition_id).rows), [('A', 13)])
        self.assertEqual(load_publication_receipt(self.db, edition_id=receipt.edition_id), receipt)
        self.assertEqual(publish_day(self.db, draft, 'amend'), receipt)

    def test_stale_parent(self):
        self.admit('a', 'upstream', ('A', 7))
        first = self.first_edition('a')
        early = draft_day(self.db, DAY, parent_id=first)
        late = draft_day(self.db, DAY, parent_id=first)
        edit_draft(self.db, early, [Amendment('correct', 1, 13)])
        edit_draft(self.db, late, [Amendment('correct', 1, 99)])
        winner = publish_day(self.db, early, 'winner')
        with self.assertRaisesRegex(ValueError, 'stale'):
            publish_day(self.db, late, 'late')
        with self.assertRaisesRegex(ValueError, 'another draft'):
            publish_day(self.db, late, 'winner')
        duplicate_initial = draft_day(self.db, DAY, batch_ids=['a'])
        with self.assertRaisesRegex(ValueError, 'stale'):
            publish_day(self.db, duplicate_initial, 'another-initial')
        self.assertEqual(get_current_edition_id(self.db, DAY), winner.edition_id)
        self.assertEqual(readings(read_day(self.db, winner.edition_id).rows), [('A', 13)])
        self.assertEqual(readings(read_day(self.db, first).rows), [('A', 7)])
        self.assertIsNone(load_publication_receipt(self.db, 'late'))
        self.assertIsNone(load_publication_receipt(self.db, 'another-initial'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM publication_receipts').fetchone(), (2,))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM editions').fetchone(), (2,))
