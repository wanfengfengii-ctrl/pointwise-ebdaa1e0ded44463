from collections.abc import Sequence
from datetime import date

from warehouse.intake.observations import SourceObservation


SQLITE_MAX_ID = (1 << 63) - 1


def validate_name(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{field} must be a nonblank string without NUL')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as error:
        raise ValueError(f'{field} must be valid Unicode text') from error


def validate_day(day: str) -> None:
    if not isinstance(day, str):
        raise ValueError('day must be a date in YYYY-MM-DD form')
    try:
        parsed = date.fromisoformat(day)
    except ValueError as error:
        raise ValueError('day must be a date in YYYY-MM-DD form') from error
    if parsed.isoformat() != day:
        raise ValueError('day must be a date in YYYY-MM-DD form')


def validate_record_id(value: int, field: str) -> None:
    if type(value) is not int or not 1 <= value <= SQLITE_MAX_ID:
        raise ValueError(f'{field} must be a positive SQLite integer')


def validate_reading(value: int | None) -> int | None:
    if value is not None and type(value) is not int:
        raise ValueError('reading must be an integer number of liters or None')
    return value


def validate_batch_input(
    station: str, day: str, batch_id: str, rows: Sequence[SourceObservation],
) -> tuple[SourceObservation, ...]:
    validate_name(station, 'station')
    validate_day(day)
    validate_name(batch_id, 'batch ID')
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise ValueError('batch rows must be an ordered sequence')
    observations = tuple(rows)
    if not observations:
        raise ValueError('batch must contain at least one observation')
    for row in observations:
        if not isinstance(row, SourceObservation):
            raise ValueError('batch rows must be SourceObservation instances')
        validate_name(row.label, 'observation label')
        validate_reading(row.liters)
    return observations
