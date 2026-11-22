"""Demonstrate the same-parent merge: preview, confirm, fixed read and night shift."""
import json
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.support import DAY, connect, initialize
from warehouse.delivery.paging import take_page
from warehouse.delivery.sessions import open_export
from warehouse.drafts.builder import draft_day
from warehouse.drafts.edits import Amendment, edit_draft
from warehouse.drafts.merge import preview_merge
from warehouse.drafts.store import load_draft
from warehouse.intake.batches import receive
from warehouse.intake.observations import Origin, SourceObservation
from warehouse.publication.publish import confirm_merge, publish_day
from warehouse.reconcile.daybook import read_day


def _page(page):
    return {
        'edition': page.edition_id,
        'number': page.page_no,
        'liters': page.total_liters,
        'rows': [[row.observation.label, row.observation.liters] for row in page.rows],
    }


def _statement(statement):
    return {
        'rows': [[row.observation.label, row.observation.liters] for row in statement.rows],
        'totals': asdict(statement.totals),
        'stations': {station.station: asdict(station.totals) for station in statement.stations},
    }


def exercise_merge(db):
    receive(db, 'upstream', DAY, 'a', [SourceObservation('A', 7)])
    receive(db, 'downstream', DAY, 'bm', [SourceObservation('B', 11), SourceObservation('M', None)])
    receive(db, 'upstream', DAY, 'c', [SourceObservation('C', 5)])
    initial = draft_day(db, DAY, batch_ids=['a', 'bm'])
    parent = publish_day(db, initial, 'first').edition_id
    open_export(db, 'night', DAY)
    original_page = take_page(db, 'night', 1, 1)

    # A starts on the night's first page before the supervisor merges two drafts.
    left = draft_day(db, DAY, parent_id=parent)
    right = draft_day(db, DAY, parent_id=parent)
    edit_draft(db, left, [Amendment('move', 1, 2)])
    edit_draft(db, right, [
        Amendment('correct', 1, 13),
        Amendment('insert', 4, Origin('upstream', 'c', 1)),
    ])

    preview = preview_merge(db, left, right)
    if preview['conflict'] is not None:
        raise AssertionError(f'unexpected merge conflict: {preview["conflict"]!r}')
    receipt = confirm_merge(db, preview['preview_id'], 'night-merge')
    published = read_day(db, receipt.edition_id)

    continued = [take_page(db, 'night', page_no, 1) for page_no in (2, 3, 4)]
    replay = take_page(db, 'night', 1, 1)
    parent_statement = read_day(db, parent)
    original_draft = read_draft_rows(db, left)
    corrected_draft = read_draft_rows(db, right)

    preview_rows = [[row.observation.label, row.observation.liters] for row in preview['rows']]
    expected_rows = [['B', 11], ['A', 13], ['M', None], ['C', 5]]
    if preview_rows != expected_rows:
        raise AssertionError(f'wrong preview: {preview_rows!r}')
    if _statement(published)['rows'] != expected_rows:
        raise AssertionError('published edition drifted from the preview')
    if published.totals != type(published.totals)(4, 1, 29):
        raise AssertionError('published totals are wrong')
    expected_pages = [
        {'edition': receipt.edition_id, 'number': 2, 'liters': 11, 'rows': [['B', 11]]},
        {'edition': receipt.edition_id, 'number': 3, 'liters': 0, 'rows': [['M', None]]},
        {'edition': receipt.edition_id, 'number': 4, 'liters': 5, 'rows': [['C', 5]]},
    ]
    if [_page(page) for page in continued] != expected_pages:
        raise AssertionError('night shift continuation is wrong')
    if _page(replay) != {'edition': parent, 'number': 1, 'liters': 7, 'rows': [['A', 7]]}:
        raise AssertionError('original page was not retained')
    if original_draft != [['B', 11], ['A', 7], ['M', None]]:
        raise AssertionError('left draft was modified by the merge')
    if corrected_draft != [['A', 13], ['B', 11], ['M', None], ['C', 5]]:
        raise AssertionError('right draft was modified by the merge')

    return {
        'case_id': 'merge-workday',
        'parent_edition': parent,
        'merged_edition': receipt.edition_id,
        'preview_rows': preview_rows,
        'preview_parent_positions': [row.parent_position for row in preview['rows']],
        'merged_statement': _statement(published),
        'parent_statement': _statement(parent_statement),
        'original_page': _page(replay),
        'night_pages': [_page(page) for page in continued],
        'left_draft_rows': original_draft,
        'right_draft_rows': corrected_draft,
        'code_paths': [
            'warehouse/drafts/merge.py', 'warehouse/publication/merge.py',
            'warehouse/publication/publish.py', 'warehouse/delivery/paging.py',
        ],
        'oracle': '按父版行位与两稿的更正、移位及补登手算合版行序，再核对固定版读回与夜班续页。',
    }


def read_draft_rows(db, draft_id):
    return [[row.observation.label, row.observation.liters] for row in load_draft(db, draft_id).rows]


def main():
    with TemporaryDirectory() as directory:
        with closing(connect(Path(directory) / 'demo.sqlite3')) as db:
            initialize(db)
            result = exercise_merge(db)
    print('POINTWISE_SCENARIO ' + json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
