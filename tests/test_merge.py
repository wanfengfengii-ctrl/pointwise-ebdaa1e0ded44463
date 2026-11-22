from contextlib import closing

from tests.support import DAY, CommitInterrupted, WarehouseTest, connect, interrupt_commit, readings
from warehouse.delivery.paging import take_page
from warehouse.delivery.sessions import open_export
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.drafts.merge import preview_merge
from warehouse.drafts.store import load_draft
from warehouse.intake.observations import Origin
from warehouse.publication.current import get_current_edition_id
from warehouse.publication.merge import load_merge_preview
from warehouse.publication.publish import confirm_merge, publish_day
from warehouse.publication.receipts import load_publication_receipt
from warehouse.reconcile.daybook import read_day
from warehouse.reconcile.totals import Totals


def positions(rows):
    return [row.parent_position for row in rows]


class MergeTestBase(WarehouseTest):
    def pair(self, *batch_ids, request='parent'):
        parent = self.first_edition(*batch_ids, request=request)
        left = draft_day(self.db, DAY, parent_id=parent)
        right = draft_day(self.db, DAY, parent_id=parent)
        return parent, left, right

    def assertConflict(self, preview, kind, refs):
        self.assertIsNone(preview['preview_id'])
        self.assertEqual(preview['rows'], ())
        self.assertEqual(preview['conflict']['kind'], kind)
        self.assertEqual(tuple(preview['conflict']['refs']), tuple(refs))


class TestMergePreview(MergeTestBase):
    def test_left_relocates_and_right_corrects(self):
        self.admit('ab', 'upstream', ('A', 7), ('B', 11))
        parent, left, right = self.pair('ab')
        edit_draft(self.db, left, [Amendment('move', 1, 2)])
        edit_draft(self.db, right, [Amendment('correct', 1, 13)])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertIsInstance(preview['preview_id'], int)
        self.assertEqual(readings(preview['rows']), [('B', 11), ('A', 13)])
        self.assertEqual(positions(preview['rows']), [2, 1])
        # Preview leaves the drafts and the current edition alone.
        self.assertEqual(get_current_edition_id(self.db, DAY), parent)
        self.assertEqual(readings(load_draft(self.db, left).rows), [('B', 11), ('A', 7)])
        self.assertEqual(readings(load_draft(self.db, right).rows), [('A', 13), ('B', 11)])

    def test_remove_move_and_remove_correct(self):
        self.admit('ab', 'upstream', ('A', 7), ('B', 11))
        parent = self.first_edition('ab')
        moved_a, moved_b, removed_a = (
            draft_day(self.db, DAY, parent_id=parent) for _ in range(3)
        )
        edit_draft(self.db, moved_a, [Amendment('move', 1, 2)])
        edit_draft(self.db, moved_b, [Amendment('move', 2, 1)])
        edit_draft(self.db, removed_a, [Amendment('remove', 1)])
        self.assertConflict(
            preview_merge(self.db, moved_a, removed_a), 'remove_move', [('parent', 1)],
        )
        self.assertConflict(
            preview_merge(self.db, removed_a, moved_a), 'remove_move', [('parent', 1)],
        )
        merged = preview_merge(self.db, moved_b, removed_a)
        self.assertIsNone(merged['conflict'])
        self.assertEqual(readings(merged['rows']), [('B', 11)])

        corrected = draft_day(self.db, DAY, parent_id=parent)
        edit_draft(self.db, corrected, [Amendment('correct', 1, 13)])
        self.assertConflict(
            preview_merge(self.db, removed_a, corrected), 'remove_correct', [('parent', 1)],
        )

    def test_same_value_correction_kept_once(self):
        self.admit('abc', 'upstream', ('A', 7), ('B', 11), ('C', 3))
        parent, left, right = self.pair('abc')
        edit_draft(self.db, left, [Amendment('correct', 1, 13)])
        edit_draft(self.db, right, [Amendment('correct', 1, 13)])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertEqual(readings(preview['rows']), [('A', 13), ('B', 11), ('C', 3)])
        self.assertEqual(positions(preview['rows']), [1, 2, 3])

    def test_differing_corrections_conflict(self):
        self.admit('a', 'upstream', ('A', 7))
        parent, left, right = self.pair('a')
        edit_draft(self.db, left, [Amendment('correct', 1, 13)])
        edit_draft(self.db, right, [Amendment('correct', 1, 17)])
        self.assertConflict(preview_merge(self.db, left, right), 'reading', [('parent', 1)])

    def test_none_and_zero_are_distinct_readings(self):
        self.admit('a', 'upstream', ('A', 7))
        parent, left, right = self.pair('a')
        edit_draft(self.db, left, [Amendment('correct', 1, None)])
        edit_draft(self.db, right, [Amendment('correct', 1, 0)])
        self.assertConflict(preview_merge(self.db, left, right), 'reading', [('parent', 1)])

    def test_uncorrected_peer_does_not_conflict(self):
        self.admit('a', 'upstream', ('A', 7))
        parent, left, right = self.pair('a')
        edit_draft(self.db, right, [Amendment('correct', 1, 13)])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertEqual(readings(preview['rows']), [('A', 13)])

    def test_both_withdraw_only_removes_once(self):
        self.admit('abc', 'upstream', ('A', 7), ('B', 11), ('C', 3))
        parent, left, right = self.pair('abc')
        edit_draft(self.db, left, [Amendment('remove', 1)])
        edit_draft(self.db, right, [Amendment('remove', 1)])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertEqual(readings(preview['rows']), [('B', 11), ('C', 3)])

    def test_in_place_move_is_not_a_relocation(self):
        self.admit('ab', 'upstream', ('A', 7), ('B', 11))
        parent, left, right = self.pair('ab')
        edit_draft(self.db, left, [Amendment('remove', 1)])
        edit_draft(self.db, right, [Amendment('move', 1, 1)])
        self.assertIsNone(preview_merge(self.db, left, right)['conflict'])
        # Moving a row away and back leaves no inversion either.
        moved_back = draft_day(self.db, DAY, parent_id=parent)
        edit_draft(self.db, moved_back, [Amendment('move', 1, 2), Amendment('move', 2, 1)])
        self.assertIsNone(preview_merge(self.db, left, moved_back)['conflict'])

    def test_order_cycle_reports_cycle_members(self):
        self.admit('abc', 'upstream', ('A', 7), ('B', 11), ('C', 3))
        parent, left, right = self.pair('abc')
        edit_draft(self.db, left, [Amendment('move', 1, 2)])      # B, A, C
        edit_draft(self.db, right, [Amendment('move', 3, 2)])     # A, C, B
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['preview_id'])
        self.assertEqual(preview['conflict']['kind'], 'order')
        refs = set(preview['conflict']['refs'])
        self.assertEqual(refs, {('parent', 1), ('parent', 2), ('parent', 3)})
        self.assertEqual(len(preview['conflict']['refs']), 3)

    def test_supplements_keep_occurrences_and_left_first(self):
        self.admit('abc', 'upstream', ('A', 7), ('B', 11), ('C', 3))
        self.admit('x', 'upstream', ('C', 5))
        parent, left, right = self.pair('abc')
        origin = Origin('upstream', 'x', 1)
        edit_draft(self.db, left, [Amendment('insert', 2, origin)])
        edit_draft(self.db, right, [Amendment('insert', 2, origin)])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertEqual(readings(preview['rows']),
                         [('A', 7), ('C', 5), ('C', 5), ('B', 11), ('C', 3)])
        self.assertEqual(positions(preview['rows']), [1, None, None, 2, 3])
        self.assertEqual([row.origin for row in preview['rows'][1:3]], [origin, origin])

    def test_unconstrained_supplements_prefer_left(self):
        self.admit('ab', 'upstream', ('A', 7), ('B', 11))
        self.admit('c', 'upstream', ('C', 5))
        self.admit('d', 'upstream', ('D', 6))
        parent, left, right = self.pair('ab')
        edit_draft(self.db, left, [Amendment('insert', 1, Origin('upstream', 'c', 1))])
        edit_draft(self.db, right, [Amendment('insert', 1, Origin('upstream', 'd', 1))])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertEqual(readings(preview['rows']), [('C', 5), ('D', 6), ('A', 7), ('B', 11)])

    def test_all_withdrawn_preview_is_empty(self):
        self.admit('ab', 'upstream', ('A', 7), ('B', 11))
        parent, left, right = self.pair('ab')
        for draft in (left, right):
            edit_draft(self.db, draft, [Amendment('remove', 1), Amendment('remove', 1)])
        preview = preview_merge(self.db, left, right)
        self.assertIsNone(preview['conflict'])
        self.assertEqual(preview['rows'], ())

    def test_conflict_draft_can_be_fixed_and_repreviewed(self):
        self.admit('a', 'upstream', ('A', 7))
        parent, left, right = self.pair('a')
        edit_draft(self.db, left, [Amendment('correct', 1, 13)])
        edit_draft(self.db, right, [Amendment('correct', 1, 17)])
        self.assertConflict(preview_merge(self.db, left, right), 'reading', [('parent', 1)])
        edit_draft(self.db, right, [Amendment('correct', 1, 13)])
        self.assertIsNone(preview_merge(self.db, left, right)['conflict'])

    def test_rejects_ineligible_drafts(self):
        self.admit('a', 'upstream', ('A', 7))
        initial = draft_day(self.db, DAY, batch_ids=['a'])
        parent = publish_day(self.db, initial, 'first').edition_id
        capable = draft_day(self.db, DAY, parent_id=parent)
        with self.assertRaises(ValueError):
            preview_merge(self.db, capable, capable)
        with self.assertRaises(ValueError):
            preview_merge(self.db, initial, capable)
        with self.assertRaises(ValueError):
            preview_merge(self.db, capable, initial)

        # A fixed-parent draft created before the merge feature has no capability row.
        legacy = self.db.execute(
            'INSERT INTO drafts (day, parent_id) VALUES (?, ?)', (DAY, parent),
        ).lastrowid
        self.db.execute(
            '''INSERT INTO draft_rows
               (draft_id, position, label, liters, station, source_batch, source_position,
                parent_position)
               VALUES (?, 1, 'A', '7', 'upstream', 'a', 1, 1)''', (legacy,),
        )
        with self.assertRaises(ValueError):
            preview_merge(self.db, capable, legacy)

        # Drafts of another parent are refused.
        other_parent_draft = draft_day(self.db, DAY, parent_id=parent)
        successor = publish_day(self.db, other_parent_draft, 'successor').edition_id
        later = draft_day(self.db, DAY, parent_id=successor)
        with self.assertRaises(ValueError):
            preview_merge(self.db, capable, later)


class TestMergeConfirm(MergeTestBase):
    def ready_merge(self):
        self.admit('ab', 'upstream', ('A', 7), ('B', 11))
        parent, left, right = self.pair('ab')
        edit_draft(self.db, left, [Amendment('move', 1, 2)])
        edit_draft(self.db, right, [Amendment('correct', 1, 13)])
        preview = preview_merge(self.db, left, right)
        return parent, left, right, preview

    def test_confirmation_publishes_frozen_preview(self):
        parent, left, right, preview = self.ready_merge()
        receipt = confirm_merge(self.db, preview['preview_id'], 'merge-request')
        self.assertEqual(receipt.request_id, 'merge-request')
        self.assertEqual(receipt.preview_id, preview['preview_id'])
        self.assertIsInstance(receipt.token, str)
        self.assertTrue(receipt.token)
        self.assertEqual(get_current_edition_id(self.db, DAY), receipt.edition_id)
        statement = read_day(self.db, receipt.edition_id)
        self.assertEqual(readings(statement.rows), [('B', 11), ('A', 13)])
        self.assertEqual([row.origin for row in statement.rows],
                         [Origin('upstream', 'ab', 2), Origin('upstream', 'ab', 1)])
        self.assertEqual(statement.totals, Totals(2, 0, 24))
        links = dict(self.db.execute(
            'SELECT position, parent_position FROM edition_links WHERE edition_id = ?',
            (receipt.edition_id,),
        ))
        self.assertEqual(links, {1: 2, 2: 1})
        # The parent edition and its receipt remain intact.
        self.assertEqual(readings(read_day(self.db, parent).rows), [('A', 7), ('B', 11)])
        self.assertIsNotNone(load_publication_receipt(self.db, edition_id=parent))

    def test_replay_returns_original_receipt(self):
        parent, left, right, preview = self.ready_merge()
        receipt = confirm_merge(self.db, preview['preview_id'], 'replay')
        self.assertEqual(confirm_merge(self.db, preview['preview_id'], 'replay'), receipt)
        # A new preview with the same request number is refused.
        another = preview_merge(self.db, left, right)
        with self.assertRaises(ValueError):
            confirm_merge(self.db, another['preview_id'], 'replay')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM merge_receipts').fetchone(), (1,))

    def test_replay_survives_non_current_parent(self):
        parent, left, right, preview = self.ready_merge()
        receipt = confirm_merge(self.db, preview['preview_id'], 'replay-late')
        successor = draft_day(self.db, DAY, parent_id=receipt.edition_id)
        edit_draft(self.db, successor, [Amendment('correct', 2, 19)])
        publish_day(self.db, successor, 'successor')
        calls = []
        self.assertEqual(
            confirm_merge(self.db, preview['preview_id'], 'replay-late',
                          before_commit=lambda: calls.append('hook')),
            receipt,
        )
        self.assertEqual(calls, [])

    def test_edit_after_preview_invalidates(self):
        parent, left, right, preview = self.ready_merge()
        edit_draft(self.db, right, [Amendment('correct', 1, 17), Amendment('correct', 1, 13)])
        with self.assertRaises(ValueError):
            confirm_merge(self.db, preview['preview_id'], 'stale')
        self.assertEqual(get_current_edition_id(self.db, DAY), parent)
        self.assertIsNone(load_publication_receipt(self.db, 'stale'))
        self.assertIsNone(self.db.execute(
            'SELECT edition_id FROM merge_receipts WHERE request_id = ?', ('stale',),
        ).fetchone())

    def test_empty_and_rejected_edits_keep_preview(self):
        parent, left, right, preview = self.ready_merge()
        edit_draft(self.db, right, [])
        with self.assertRaises(ValueError):
            edit_draft(self.db, right, [Amendment('move', 99, 1)])
        receipt = confirm_merge(self.db, preview['preview_id'], 'still-valid')
        self.assertEqual(readings(read_day(self.db, receipt.edition_id).rows),
                         [('B', 11), ('A', 13)])

    def test_competing_publish_invalidates(self):
        parent, left, right, preview = self.ready_merge()
        rival = draft_day(self.db, DAY, parent_id=parent)
        edit_draft(self.db, rival, [Amendment('correct', 2, 12)])
        publish_day(self.db, rival, 'rival')
        with self.assertRaises(ValueError):
            confirm_merge(self.db, preview['preview_id'], 'losing-merge')
        self.assertIsNone(load_publication_receipt(self.db, 'losing-merge'))
        self.assertIsNone(self.db.execute(
            'SELECT edition_id FROM merge_receipts WHERE request_id = ?', ('losing-merge',),
        ).fetchone())

    def test_commit_hook_failure_can_retry_same_request(self):
        parent, left, right, preview = self.ready_merge()
        with self.assertRaises(CommitInterrupted):
            confirm_merge(self.db, preview['preview_id'], 'retry', before_commit=interrupt_commit)
        self.assertEqual(get_current_edition_id(self.db, DAY), parent)
        self.assertIsNone(load_publication_receipt(self.db, 'retry'))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM merge_receipts').fetchone(), (0,))
        receipt = confirm_merge(self.db, preview['preview_id'], 'retry')
        self.assertEqual(get_current_edition_id(self.db, DAY), receipt.edition_id)
        self.assertEqual(readings(read_day(self.db, receipt.edition_id).rows), [('B', 11), ('A', 13)])

    def test_parameter_errors(self):
        parent, left, right, preview = self.ready_merge()
        for invalid in (0, -1, True, 1.5, 'x'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    confirm_merge(self.db, invalid, 'request')
        with self.assertRaises(ValueError):
            confirm_merge(self.db, preview['preview_id'], '   ')
        with self.assertRaises(LookupError):
            confirm_merge(self.db, 999, 'missing-preview')

    def test_preview_and_confirmation_cross_reopen(self):
        parent, left, right, preview = self.ready_merge()
        with closing(connect(self.path)) as reopened:
            self.assertEqual(readings(load_merge_preview(reopened, preview['preview_id']).rows),
                             [('B', 11), ('A', 13)])
            receipt = confirm_merge(reopened, preview['preview_id'], 'across-reopen')
        with closing(connect(self.path)) as reopened:
            self.assertEqual(confirm_merge(reopened, preview['preview_id'], 'across-reopen'), receipt)
            self.assertEqual(readings(read_day(reopened, receipt.edition_id).rows),
                             [('B', 11), ('A', 13)])


class TestMergeDelivery(MergeTestBase):
    def test_night_shift_continues_after_confirmation(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('bm', 'downstream', ('B', 11), ('M', None))
        self.admit('c', 'upstream', ('C', 5))
        initial = draft_day(self.db, DAY, batch_ids=['a', 'bm'])
        parent = publish_day(self.db, initial, 'first').edition_id
        open_export(self.db, 'night', DAY)
        original = take_page(self.db, 'night', 1, 1)
        self.assertEqual((original.edition_id, readings(original.rows)), (parent, [('A', 7)]))

        left = draft_day(self.db, DAY, parent_id=parent)
        right = draft_day(self.db, DAY, parent_id=parent)
        edit_draft(self.db, left, [Amendment('move', 1, 2)])
        edit_draft(self.db, right, [
            Amendment('correct', 1, 13), Amendment('insert', 4, Origin('upstream', 'c', 1)),
        ])
        preview = preview_merge(self.db, left, right)
        self.assertEqual(readings(preview['rows']),
                         [('B', 11), ('A', 13), ('M', None), ('C', 5)])
        receipt = confirm_merge(self.db, preview['preview_id'], 'merge-night')
        statement = read_day(self.db, receipt.edition_id)
        self.assertEqual(statement.totals, Totals(4, 1, 29))

        self.assertEqual(take_page(self.db, 'night', 1, 1), original)
        following = [take_page(self.db, 'night', page_no, 1) for page_no in (2, 3, 4)]
        self.assertEqual([readings(page.rows) for page in following],
                         [[('B', 11)], [('M', None)], [('C', 5)]])
        self.assertEqual([page.total_liters for page in following], [11, 0, 5])
        self.assertTrue(all(page.edition_id == receipt.edition_id for page in following))
        self.assertEqual(readings(read_day(self.db, parent).rows), [('A', 7), ('B', 11), ('M', None)])
        self.assertEqual(readings(load_draft(self.db, left).rows), [('B', 11), ('A', 7), ('M', None)])
        self.assertEqual(readings(load_draft(self.db, right).rows),
                         [('A', 13), ('B', 11), ('M', None), ('C', 5)])

    def test_commit_failure_across_reopen_then_replay(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('bm', 'downstream', ('B', 11), ('M', None))
        self.admit('c', 'upstream', ('C', 5))
        parent = self.first_edition('a', 'bm')
        open_export(self.db, 'night', DAY)
        original = take_page(self.db, 'night', 1, 1)
        left = draft_day(self.db, DAY, parent_id=parent)
        right = draft_day(self.db, DAY, parent_id=parent)
        edit_draft(self.db, left, [Amendment('move', 1, 2)])
        edit_draft(self.db, right, [
            Amendment('correct', 1, 13), Amendment('insert', 4, Origin('upstream', 'c', 1)),
        ])
        preview = preview_merge(self.db, left, right)
        self.db.close()

        with closing(connect(self.path)) as reopened:
            with self.assertRaises(CommitInterrupted):
                confirm_merge(
                    reopened, preview['preview_id'], 'across',
                    before_commit=interrupt_commit,
                )
            self.assertEqual(get_current_edition_id(reopened, DAY), parent)
            self.assertIsNone(load_publication_receipt(reopened, 'across'))
            self.assertEqual(take_page(reopened, 'night', 1, 1), original)

        with closing(connect(self.path)) as reopened:
            receipt = confirm_merge(reopened, preview['preview_id'], 'across')
            self.assertEqual(readings(read_day(reopened, receipt.edition_id).rows),
                             [('B', 11), ('A', 13), ('M', None), ('C', 5)])
            self.assertEqual(take_page(reopened, 'night', 1, 1), original)
            continued = [take_page(reopened, 'night', page_no, 1) for page_no in (2, 3, 4)]
            self.assertEqual([readings(page.rows) for page in continued],
                             [[('B', 11)], [('M', None)], [('C', 5)]])

        with closing(connect(self.path)) as reopened:
            self.assertEqual(confirm_merge(reopened, preview['preview_id'], 'across'), receipt)
            self.assertEqual(readings(read_day(reopened, receipt.edition_id).rows),
                             [('B', 11), ('A', 13), ('M', None), ('C', 5)])
            self.assertEqual(take_page(reopened, 'night', 1, 1), original)
            self.assertEqual([readings(take_page(reopened, 'night', page_no, 1).rows)
                              for page_no in (2, 3, 4)],
                             [[('B', 11)], [('M', None)], [('C', 5)]])
