from tests.support import DAY, OTHER_DAY, WarehouseTest, readings
from warehouse.delivery.paging import take_page
from warehouse.delivery.sessions import open_export
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.intake.observations import Origin
from warehouse.publication.publish import publish_day
from warehouse.reconcile.comparison import compare_days
from warehouse.reconcile.daybook import read_day
from warehouse.reconcile.totals import Totals


def station_totals(statement):
    return {station.station: station.totals for station in statement.stations}


class TestReconciliation(WarehouseTest):
    def test_missing_and_amended(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('m', 'downstream', ('M', None))
        before = self.first_edition('a', 'm')
        draft = draft_day(self.db, DAY, parent_id=before)
        edit_draft(self.db, draft, [Amendment('correct', 1, 13)])
        after = publish_day(self.db, draft, 'adjust').edition_id
        report = compare_days(self.db, before, after)
        self.assertEqual(report.before, read_day(self.db, before))
        self.assertEqual(report.after, read_day(self.db, after))
        self.assertEqual(readings(report.before.rows), [('A', 7), ('M', None)])
        self.assertEqual(readings(report.after.rows), [('A', 13), ('M', None)])
        origins = [Origin('upstream', 'a', 1), Origin('downstream', 'm', 1)]
        self.assertEqual([row.origin for row in report.before.rows], origins)
        self.assertEqual([row.origin for row in report.after.rows], origins)
        self.assertEqual(report.before.totals, Totals(2, 1, 7))
        self.assertEqual(report.after.totals, Totals(2, 1, 13))
        self.assertEqual(report.total_delta, Totals(0, 0, 6))
        self.assertEqual(station_totals(report.before), {
            'upstream': Totals(1, 0, 7), 'downstream': Totals(1, 1, 0),
        })
        self.assertEqual(station_totals(report.after), {
            'upstream': Totals(1, 0, 13), 'downstream': Totals(1, 1, 0),
        })

    def test_zero_vs_missing(self):
        self.admit('z', 'upstream', ('Z', 0), ('M', None))
        edition = self.first_edition('z')
        statement = read_day(self.db, edition)
        self.assertEqual(readings(statement.rows), [('Z', 0), ('M', None)])
        self.assertEqual(station_totals(statement), {'upstream': Totals(2, 1, 0)})

    def test_station_union(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('m', 'middle', ('M', None))
        self.admit('c', 'downstream', ('C', 5))
        before = self.first_edition('a', 'm')
        draft = draft_day(self.db, DAY, parent_id=before)
        edit_draft(self.db, draft, [Amendment('remove', 1), Amendment('insert', 2, Origin('downstream', 'c', 1))])
        after = publish_day(self.db, draft, 'station-change').edition_id
        report = compare_days(self.db, before, after)
        self.assertEqual(set(station_totals(report.before)), {'upstream', 'middle'})
        self.assertEqual(set(station_totals(report.after)), {'middle', 'downstream'})
        self.assertEqual([(entry.station, entry.delta) for entry in report.station_deltas], [
            ('downstream', Totals(1, 0, 5)), ('middle', Totals()), ('upstream', Totals(-1, 0, -7)),
        ])
        self.assertEqual(report.total_delta, Totals(0, 0, -2))

    def test_fixed_lookup_and_cross_day_rejection(self):
        self.admit('a', 'upstream', ('A', 7))
        self.admit('b', 'upstream', ('B', 11), day=OTHER_DAY)
        before = self.first_edition('a')
        different_day = self.first_edition('b', day=OTHER_DAY, request='other-day')
        with self.assertRaisesRegex(ValueError, 'same day'):
            compare_days(self.db, before, different_day)
        with self.assertRaises(LookupError):
            compare_days(self.db, before, 999)
        with self.assertRaises(LookupError):
            read_day(self.db, 999)

    def test_exact_integer_storage_and_page_subtotal(self):
        large = 1 << 20000
        self.admit('large', 'upstream', ('P', large + 7), ('N', -large), ('Q', large))
        edition = self.first_edition('large')
        expected = large + 7
        self.assertEqual(station_totals(read_day(self.db, edition)), {'upstream': Totals(3, 0, expected)})
        open_export(self.db, 'night', DAY)
        page = take_page(self.db, 'night', 1, large)
        self.assertEqual(page.total_liters, expected)
        self.assertEqual(readings(page.rows), [('P', large + 7), ('N', -large), ('Q', large)])
        self.assertEqual(take_page(self.db, 'night', 1, large), page)
