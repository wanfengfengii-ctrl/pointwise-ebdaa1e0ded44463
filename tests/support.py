import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from warehouse.drafts.builder import draft_day
from warehouse.intake.batches import receive
from warehouse.intake.observations import SourceObservation
from warehouse.publication.publish import publish_day


ROOT = Path(__file__).resolve().parents[1]
DAY = '2026-10-06'
OTHER_DAY = '2026-10-07'


def connect(path: Path, *, timeout: float = 5) -> sqlite3.Connection:
    db = sqlite3.connect(path, isolation_level=None, timeout=timeout)
    db.execute('PRAGMA foreign_keys = ON')
    return db


def initialize(db: sqlite3.Connection) -> None:
    db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))


def readings(rows):
    return [(row.observation.label, row.observation.liters) for row in rows]


class CommitInterrupted(RuntimeError):
    pass


class ReplyLost(RuntimeError):
    pass


def interrupt_commit() -> None:
    raise CommitInterrupted('interrupted before commit')


def lose_reply() -> None:
    raise ReplyLost('reply lost after commit')


class WarehouseTest(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'warehouse.sqlite3'
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        initialize(self.db)

    def admit(self, name, station, *rows, day=DAY):
        return receive(self.db, station, day, name, tuple(SourceObservation(*row) for row in rows))

    def first_edition(self, *batch_ids, day=DAY, request='first'):
        draft = draft_day(self.db, day, batch_ids=batch_ids)
        return publish_day(self.db, draft, request).edition_id
