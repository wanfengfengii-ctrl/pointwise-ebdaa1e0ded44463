import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support import ROOT, WarehouseTest, connect, initialize
from warehouse.delivery.paging import take_page
from warehouse.delivery.sessions import open_export
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.intake.batches import receive
from warehouse.intake.observations import Origin, SourceObservation
from warehouse.publication.publish import publish_day
from warehouse.publication.receipts import load_publication_receipt
from warehouse.reconcile.comparison import compare_days
from warehouse.reconcile.daybook import read_day


def sample_input():
    return json.loads((ROOT / 'examples' / 'workday.json').read_text(encoding='utf-8'))


def page_summary(page):
    return {
        'edition': page.edition_id,
        'number': page.page_no,
        'liters': page.total_liters,
        'rows': [
            [row.observation.label, row.observation.liters, row.origin.station,
             row.origin.batch_id, row.origin.line_no]
            for row in page.rows
        ],
    }


def statement_summary(statement):
    return {
        'rows': [[row.observation.label, row.observation.liters] for row in statement.rows],
        'totals': asdict(statement.totals),
        'stations': {station.station: asdict(station.totals) for station in statement.stations},
    }


def exercise_workday(db, data):
    day = data['day']
    batches = data['batches']
    for batch in batches:
        receipt = receive(db, batch['station'], day, batch['id'], [
            SourceObservation(label, liters) for label, liters in batch['rows']
        ])
        if receipt.batch_id != batch['id'] or receipt.row_count != len(batch['rows']) or not receipt.token:
            raise AssertionError('station receipt did not acknowledge all original rows')
    draft_id = draft_day(db, day, batch_ids=data['initial_batches'])
    initial = publish_day(db, draft_id, 'morning')
    session = open_export(db, data['session'], day)
    first = take_page(db, session.session_id, 1, 1)
    amend = draft_day(db, day, parent_id=initial.edition_id)
    edit_draft(db, amend, [
        Amendment(operation, position, Origin(*argument) if operation == 'insert' else argument)
        for operation, position, argument in data['amendments']
    ])
    revised = publish_day(db, amend, 'amended')
    pages = [first] + [take_page(db, session.session_id, page_no, 1) for page_no in range(2, 6)]
    replay = take_page(db, session.session_id, 1, 1)
    before = read_day(db, initial.edition_id)
    after = read_day(db, revised.edition_id)
    comparison = compare_days(db, initial.edition_id, revised.edition_id)

    # The expected rows and counts are computed by hand from the sample's original slips.
    expected = [
        {'edition': 1, 'number': 1, 'liters': 7, 'rows': [['A', 7, 'upstream', 'a', 1]]},
        {'edition': 2, 'number': 2, 'liters': 5, 'rows': [['C', 5, 'upstream', 'c', 1]]},
        {'edition': 2, 'number': 3, 'liters': 13, 'rows': [['B', 13, 'downstream', 'bm', 1]]},
        {'edition': 2, 'number': 4, 'liters': 0, 'rows': [['M', None, 'downstream', 'bm', 2]]},
        {'edition': 2, 'number': 5, 'liters': 0, 'rows': []},
    ]
    downstream_expected = {
        'before': {
            'rows': [['A', 7], ['B', 11], ['M', None]],
            'totals': {'rows': 3, 'missing': 1, 'liters': 18},
            'stations': {
                'upstream': {'rows': 1, 'missing': 0, 'liters': 7},
                'downstream': {'rows': 2, 'missing': 1, 'liters': 11},
            },
        },
        'after': {
            'rows': [['C', 5], ['B', 13], ['A', 7], ['M', None]],
            'totals': {'rows': 4, 'missing': 1, 'liters': 25},
            'stations': {
                'upstream': {'rows': 2, 'missing': 0, 'liters': 12},
                'downstream': {'rows': 2, 'missing': 1, 'liters': 13},
            },
        },
        'station_deltas': {
            'upstream': {'rows': 1, 'missing': 0, 'liters': 5},
            'downstream': {'rows': 0, 'missing': 0, 'liters': 2},
        },
        'day_delta': {'rows': 1, 'missing': 0, 'liters': 7},
        'publication_receipts_retained': True,
        'original_page_retained': True,
        'replay': expected[0],
    }
    actual = [page_summary(page) for page in pages]
    downstream_actual = {
        'before': statement_summary(before),
        'after': statement_summary(after),
        'station_deltas': {entry.station: asdict(entry.delta) for entry in comparison.station_deltas},
        'day_delta': asdict(comparison.total_delta),
        'publication_receipts_retained': (
            load_publication_receipt(db, 'morning') == initial
            and load_publication_receipt(db, edition_id=revised.edition_id) == revised
        ),
        'original_page_retained': replay == first and bool(replay.receipt.token),
        'replay': page_summary(replay),
    }
    if comparison.before != before or comparison.after != after:
        raise AssertionError('comparison did not use the two fixed statements')
    if actual != expected:
        raise AssertionError(f'wrong issue sequence: {actual!r}')
    if downstream_actual != downstream_expected:
        raise AssertionError(f'wrong reconciliation: {downstream_actual!r}')
    return {
        'case_id': 'n6',
        'input': data,
        'expected': expected,
        'actual': actual,
        'downstream_expected': downstream_expected,
        'downstream_actual': downstream_actual,
        'code_paths': [
            'warehouse/intake/batches.py', 'warehouse/drafts/edits.py', 'warehouse/publication/publish.py',
            'warehouse/delivery/paging.py', 'warehouse/reconcile/daybook.py', 'warehouse/reconcile/comparison.py',
        ],
        'oracle': '按原单和编辑顺序手算两版、逐页发行结果和各站差值，逐字段核对复领。',
    }


class TestWorkday(WarehouseTest):
    def test_all_roles_share_the_daybook(self):
        exercise_workday(self.db, sample_input())


def main():
    with TemporaryDirectory() as directory:
        with closing(connect(Path(directory) / 'demo.sqlite3')) as db:
            initialize(db)
            result = exercise_workday(db, sample_input())
    print('POINTWISE_SCENARIO ' + json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
