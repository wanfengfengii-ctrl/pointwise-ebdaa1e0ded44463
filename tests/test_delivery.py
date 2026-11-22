import sqlite3
from contextlib import closing

from tests.support import (
    DAY, OTHER_DAY, CommitInterrupted, ReplyLost, WarehouseTest, connect, initialize,
    interrupt_commit, lose_reply, readings,
)
from warehouse.delivery.page_store import load_successful_page, read_delivery_evidence
from warehouse.delivery.paging import take_page
from warehouse.delivery.sessions import open_export
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.drafts.store import load_draft
from warehouse.intake.batches import receive
from warehouse.intake.observations import Origin, SourceObservation
from warehouse.publication.publish import publish_day
from warehouse.reconcile.daybook import read_day
from warehouse.reconcile.totals import Totals


class TestDelivery(WarehouseTest):
    def start_handover(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('b', 'downstream', ('B', 11))
        self.admit('c', 'upstream', ('C', 5))
        base = self.first_edition('a', 'b')
        open_export(self.db, 'night', DAY)
        first = take_page(self.db, 'night', 1, 1)
        self.assertEqual((first.edition_id, readings(first.rows), first.total_liters), (base, [('A', 7)], 7))
        self.assertEqual(first.rows[0].origin, Origin('upstream', 'a', 1))
        amend = draft_day(self.db, DAY, parent_id=base)
        edit_draft(self.db, amend, [
            Amendment('correct', 1, 13), Amendment('move', 1, 2),
            Amendment('insert', 1, Origin('upstream', 'c', 1)),
        ])
        return first, amend

    def test_first_and_next(self):
        first, amend = self.start_handover()
        edition = publish_day(self.db, amend, 'amended').edition_id
        self.assertEqual(take_page(self.db, 'night', 1, 1), first)
        second = take_page(self.db, 'night', 2, 1)
        third = take_page(self.db, 'night', 3, 1)
        fourth = take_page(self.db, 'night', 4, 1)
        self.assertEqual((readings(second.rows), second.total_liters), ([('C', 5)], 5))
        self.assertEqual((readings(third.rows), third.total_liters), ([('B', 11)], 11))
        self.assertEqual((fourth.rows, fourth.total_liters), ((), 0))
        self.assertEqual([page.edition_id for page in (second, third, fourth)], [edition] * 3)
        self.assertEqual(second.rows[0].origin, Origin('upstream', 'c', 1))
        current = read_day(self.db, edition)
        self.assertEqual(readings(current.rows), [('C', 5), ('B', 11), ('A', 13)])
        self.assertEqual(current.totals, Totals(3, 0, 29))

        open_export(self.db, 'other-night', DAY)
        self.assertEqual(readings(take_page(self.db, 'other-night', 1, 4).rows), readings(current.rows))

    def test_failed_page(self):
        first, amend = self.start_handover()
        edition = publish_day(self.db, amend, 'amended').edition_id
        with self.assertRaises(CommitInterrupted):
            take_page(self.db, 'night', 2, 1, before_commit=interrupt_commit)
        self.assertIsNone(load_successful_page(self.db, 'night', 2))
        self.assertEqual(read_delivery_evidence(self.db, 'night'), {first.edition_id: {1}})
        retry = take_page(self.db, 'night', 2, 1)
        self.assertEqual((retry.edition_id, readings(retry.rows), retry.total_liters), (edition, [('C', 5)], 5))
        self.assertEqual(retry, take_page(self.db, 'night', 2, 1))
        self.assertEqual(self.db.execute(
            'SELECT token FROM successful_pages WHERE session_id = ? AND page_no = ?', ('night', 2),
        ).fetchall(), [(retry.receipt.token,)])
        self.assertEqual(readings(take_page(self.db, 'night', 3, 1).rows), [('B', 11)])

    def test_reply_loss(self):
        self.admit('a', 'upstream', ('A', 7), ('B', 11))
        base = self.first_edition('a')
        session = open_export(self.db, 'night', DAY)
        with self.assertRaises(ReplyLost):
            take_page(self.db, 'night', 1, 1, after_commit=lose_reply)
        committed = load_successful_page(self.db, 'night', 1)
        self.assertIsNotNone(committed)
        amend = draft_day(self.db, DAY, parent_id=base)
        edit_draft(self.db, amend, [Amendment('correct', 1, 13), Amendment('move', 1, 2)])
        new_edition = publish_day(self.db, amend, 'amended').edition_id
        with closing(connect(self.path)) as reopened:
            self.assertEqual(open_export(reopened, 'night', DAY), session)
            self.assertEqual(take_page(reopened, 'night', 1, 1, after_commit=lose_reply), committed)
            self.assertEqual((committed.edition_id, readings(committed.rows), committed.total_liters),
                             (base, [('A', 7)], 7))
            self.assertEqual(committed.rows[0].origin, Origin('upstream', 'a', 1))
            with self.assertRaisesRegex(ValueError, 'page size differs'):
                take_page(reopened, 'night', 1, 2)
            following = take_page(reopened, 'night', 2, 3)
            self.assertEqual((following.edition_id, readings(following.rows)), (new_edition, [('B', 11)]))

    def test_amended_page_reply_loss_after_retry(self):
        first, amend = self.start_handover()
        second_edition = publish_day(self.db, amend, 'amended').edition_id
        with self.assertRaises(CommitInterrupted):
            take_page(self.db, 'night', 2, 1, before_commit=interrupt_commit)
        with self.assertRaises(ReplyLost):
            take_page(self.db, 'night', 2, 1, after_commit=lose_reply)
        sent = load_successful_page(self.db, 'night', 2)
        self.assertEqual((sent.edition_id, readings(sent.rows)), (second_edition, [('C', 5)]))
        middle = draft_day(self.db, DAY, parent_id=second_edition)
        edit_draft(self.db, middle, [Amendment('move', 1, 3)])
        middle_edition = publish_day(self.db, middle, 'move-c').edition_id
        later = draft_day(self.db, DAY, parent_id=middle_edition)
        edit_draft(self.db, later, [Amendment('correct', 3, 6), Amendment('move', 2, 1)])
        latest = publish_day(self.db, later, 'correct-c').edition_id
        with closing(connect(self.path)) as reopened:
            open_export(reopened, 'night', DAY)
            self.assertEqual(take_page(reopened, 'night', 1, 1), first)
            self.assertEqual(take_page(reopened, 'night', 2, 1), sent)
            with self.assertRaisesRegex(ValueError, 'page size differs'):
                take_page(reopened, 'night', 2, 2)
            following = take_page(reopened, 'night', 3, 1)
            self.assertEqual((following.edition_id, readings(following.rows)), (latest, [('B', 11)]))
            self.assertEqual(take_page(reopened, 'night', 4, 1).rows, ())
            self.assertEqual(readings(read_day(reopened, latest).rows), [('A', 13), ('B', 11), ('C', 6)])

    def test_identical_reentry(self):
        origin = Origin('upstream', 'a', 1)
        editions = []
        followups = []
        for name, edits in (
            ('move', [Amendment('move', 1, 2)]),
            ('reenter', [Amendment('remove', 1), Amendment('insert', 2, origin)]),
        ):
            with self.subTest(route=name), closing(connect(self.path.with_name(f'{name}.sqlite3'))) as db:
                initialize(db)
                receive(db, 'upstream', DAY, 'a', [SourceObservation('A', 7), SourceObservation('B', 11)])
                draft = draft_day(db, DAY, batch_ids=['a'])
                initial = publish_day(db, draft, 'first').edition_id
                open_export(db, 'night', DAY)
                old = take_page(db, 'night', 1, 1)
                self.assertEqual(readings(old.rows), [('A', 7)])
                amend = draft_day(db, DAY, parent_id=initial)
                edit_draft(db, amend, edits)
                new = publish_day(db, amend, 'amended').edition_id
                editions.append(read_day(db, new).rows)
                followups.append(readings(take_page(db, 'night', 2, 2).rows))
                self.assertEqual(take_page(db, 'night', 1, 1), old)
        self.assertEqual(editions[0], editions[1])
        self.assertEqual(readings(editions[0]), [('B', 11), ('A', 7)])
        self.assertEqual(editions[0][1].origin, origin)
        self.assertEqual(followups, [[('B', 11)], [('B', 11), ('A', 7)]])

    def test_session_binding(self):
        session = open_export(self.db, 'night', DAY)
        self.assertEqual(open_export(self.db, 'night', DAY), session)
        with self.assertRaisesRegex(ValueError, 'different day'):
            open_export(self.db, 'night', OTHER_DAY)
        with self.assertRaisesRegex(LookupError, 'no published edition'):
            take_page(self.db, 'night', 1, 1)
        self.admit('a', 'upstream', ('A', 7))
        edition = self.first_edition('a')
        first = take_page(self.db, 'night', 1, 1)
        self.assertEqual(first.edition_id, edition)
        self.assertEqual(open_export(self.db, 'night', DAY), session)
        self.assertEqual(take_page(self.db, 'night', 1, 1), first)

    def test_page_sequence(self):
        self.admit('a', 'upstream', ('A', 7), ('B', 11), ('M', None))
        self.first_edition('a')
        open_export(self.db, 'night', DAY)
        for page_no, page_size in ((0, 1), (True, 1), (2, 1), (1, 0), (1, True), (1, 1.5), (1 << 70, 1)):
            with self.subTest(page_no=page_no, page_size=page_size):
                with self.assertRaises(ValueError):
                    take_page(self.db, 'night', page_no, page_size)
        self.assertIsNone(load_successful_page(self.db, 'night', 1))
        first = take_page(self.db, 'night', 1, 2)
        self.assertEqual((readings(first.rows), first.total_liters), ([('A', 7), ('B', 11)], 18))
        with self.assertRaises(ValueError):
            take_page(self.db, 'night', 1, 1)
        second = take_page(self.db, 'night', 2, 1)
        self.assertEqual((readings(second.rows), second.total_liters), ([('M', None)], 0))
        self.assertEqual(take_page(self.db, 'night', 1, 2), first)

    def test_empty_page(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('c', 'upstream', ('C', 5))
        edition = self.first_edition('a')
        open_export(self.db, 'night', DAY)
        take_page(self.db, 'night', 1, 1)
        empty = take_page(self.db, 'night', 2, 1)
        self.assertEqual((empty.edition_id, empty.rows, empty.total_liters), (edition, (), 0))
        draft = draft_day(self.db, DAY, parent_id=edition)
        edit_draft(self.db, draft, [Amendment('insert', 1, Origin('upstream', 'c', 1))])
        published = publish_day(self.db, draft, 'add-c').edition_id
        self.assertEqual(take_page(self.db, 'night', 2, 1), empty)
        with self.assertRaises(ValueError):
            take_page(self.db, 'night', 2, 2)
        following = take_page(self.db, 'night', 3, 1)
        self.assertEqual((following.edition_id, readings(following.rows)), (published, [('C', 5)]))

    def test_single_edition(self):
        first, amend = self.start_handover()
        with closing(connect(self.path, timeout=0)) as publisher:
            cutovers = []

            def publish_during_call():
                cutovers.append(publish_day(publisher, amend, 'during-selection').edition_id)

            second = take_page(self.db, 'night', 2, 2, before_select=publish_during_call)
            self.assertEqual(cutovers, [second.edition_id])
            self.assertEqual(readings(second.rows), [('C', 5), ('B', 11)])
            self.assertEqual(second.total_liters, 16)
            fixed = read_day(publisher, second.edition_id)
            self.assertEqual(second.rows, fixed.rows[:2])
            self.assertEqual(take_page(self.db, 'night', 1, 1), first)

            draft = draft_day(publisher, DAY, parent_id=second.edition_id)
            edit_draft(publisher, draft, [Amendment('insert', 1, Origin('upstream', 'c', 1))])

            def attempt_concurrent_publication():
                with self.assertRaises(sqlite3.OperationalError) as error:
                    publish_day(publisher, draft, 'after-selection')
                self.assertEqual(error.exception.sqlite_errorcode, sqlite3.SQLITE_BUSY)

            empty = take_page(self.db, 'night', 3, 1, before_commit=attempt_concurrent_publication)
            self.assertEqual((empty.edition_id, empty.rows), (second.edition_id, ()))
            later = publish_day(publisher, draft, 'after-selection').edition_id
        following = take_page(self.db, 'night', 4, 1)
        self.assertEqual((following.edition_id, readings(following.rows)), (later, [('C', 5)]))
        self.assertEqual(take_page(self.db, 'night', 3, 1), empty)

    def test_same_page_committed_during_call(self):
        self.admit('a', 'upstream', ('A', 7), ('B', 11))
        self.first_edition('a')
        open_export(self.db, 'night', DAY)
        with closing(connect(self.path)) as other_reader:
            sent = []

            def commit_first():
                sent.append(take_page(other_reader, 'night', 1, 1))

            page = take_page(self.db, 'night', 1, 1, before_select=commit_first, after_commit=lose_reply)
            self.assertEqual(sent, [page])
        self.assertEqual(readings(page.rows), [('A', 7)])
        self.assertEqual(readings(take_page(self.db, 'night', 2, 1).rows), [('B', 11)])

    def test_init_reopen(self):
        first, amend = self.start_handover()
        edition = publish_day(self.db, amend, 'amended').edition_id
        second = take_page(self.db, 'night', 2, 1)
        working = draft_day(self.db, DAY, parent_id=edition)
        edited = edit_draft(self.db, working, [Amendment('correct', 2, 19)])
        with closing(connect(self.path)) as reopened:
            initialize(reopened)
            initialize(reopened)
            self.assertEqual(load_draft(reopened, working), edited)
            self.assertEqual(readings(read_day(reopened, edition).rows), [('C', 5), ('B', 11), ('A', 13)])
            open_export(reopened, 'night', DAY)
            self.assertEqual(take_page(reopened, 'night', 1, 1), first)
            self.assertEqual(take_page(reopened, 'night', 2, 1), second)
            self.assertEqual(readings(take_page(reopened, 'night', 3, 1).rows), [('B', 11)])
            self.assertEqual(reopened.execute('PRAGMA foreign_key_check').fetchall(), [])
