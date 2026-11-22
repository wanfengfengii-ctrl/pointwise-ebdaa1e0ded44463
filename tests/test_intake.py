from tests.support import DAY, OTHER_DAY, WarehouseTest, readings
from warehouse.drafts.builder import draft_day
from warehouse.intake.batches import receive
from warehouse.intake.observations import Origin, SourceObservation
from warehouse.intake.source_store import fetch_batch
from warehouse.reconcile.daybook import read_day


class TestIntake(WarehouseTest):
    def test_receive_twice(self):
        first = self.admit('a', 'upstream', ('A', 7))
        retry = self.admit('a', 'upstream', ('A', 7))
        self.assertEqual(first, retry)
        self.assertEqual((first.batch_id, first.row_count), ('a', 1))
        statement = read_day(self.db, self.first_edition('a'))
        self.assertEqual(readings(statement.rows), [('A', 7)])
        self.assertEqual(statement.rows[0].origin, Origin('upstream', first.batch_id, 1))
        self.assertEqual(fetch_batch(self.db, statement.rows[0].origin.batch_id).receipt, first)

    def test_bad_batch(self):
        for value in ('7 L', True, 7.0, {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'reading'):
                    receive(self.db, 'upstream', DAY, 'bad', [
                        SourceObservation('A', 7), SourceObservation('bad', value),
                    ])
                self.assertIsNone(fetch_batch(self.db, 'bad'))
                with self.assertRaisesRegex(LookupError, 'unknown received batch'):
                    draft_day(self.db, DAY, batch_ids=['bad'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM drafts').fetchone(), (0,))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM source_rows').fetchone(), (0,))
        self.admit('valid-missing', 'upstream', ('M', None))
        self.assertIsNone(fetch_batch(self.db, 'valid-missing').rows[0].liters)

    def test_origin_order(self):
        self.admit('up', 'upstream', ('A', 7))
        self.admit('down', 'downstream', ('B', 11), ('M', None))
        statement = read_day(self.db, self.first_edition('down', 'up'))
        self.assertEqual(readings(statement.rows), [('B', 11), ('M', None), ('A', 7)])
        self.assertEqual([row.origin for row in statement.rows], [
            Origin('downstream', 'down', 1), Origin('downstream', 'down', 2), Origin('upstream', 'up', 1),
        ])

    def test_conflicting_retry(self):
        original_rows = (('A', 7), ('M', None))
        first = self.admit('a', 'upstream', *original_rows)
        conflicts = [
            ('downstream', DAY, original_rows),
            ('upstream', OTHER_DAY, original_rows),
            ('upstream', DAY, tuple(reversed(original_rows))),
            ('upstream', DAY, (('A', 7), ('M', 0))),
            ('upstream', DAY, (('different label', 7), ('M', None))),
        ]
        for station, day, rows in conflicts:
            with self.subTest(station=station, day=day, rows=rows):
                with self.assertRaisesRegex(ValueError, 'different original'):
                    self.admit('a', station, *rows, day=day)
        self.assertEqual(self.admit('a', 'upstream', *original_rows), first)
        self.assertEqual(readings(read_day(self.db, self.first_edition('a')).rows), list(original_rows))
