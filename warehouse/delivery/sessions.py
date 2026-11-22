from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.intake.validation import validate_day, validate_name


@dataclass(frozen=True, slots=True)
class ExportSession:
    session_id: str
    day: str


def _find_session(db: Connection, session_id: str) -> ExportSession | None:
    row = db.execute('SELECT day FROM export_sessions WHERE session_id = ?', (session_id,)).fetchone()
    return None if row is None else ExportSession(session_id, row[0])


def load_session(db: Connection, session_id: str) -> ExportSession:
    validate_name(session_id, 'session ID')
    session = _find_session(db, session_id)
    if session is None:
        raise LookupError(f'unknown export session {session_id!r}')
    return session


def open_export(db: Connection, session_id: str, day: str) -> ExportSession:
    validate_name(session_id, 'session ID')
    validate_day(day)
    db.execute('BEGIN IMMEDIATE')
    with db:
        existing = _find_session(db, session_id)
        if existing is not None:
            if existing.day != day:
                raise ValueError('session ID is already bound to a different day')
            return existing
        db.execute('INSERT INTO export_sessions (session_id, day) VALUES (?, ?)', (session_id, day))
        return ExportSession(session_id, day)
