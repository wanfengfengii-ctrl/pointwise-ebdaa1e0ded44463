from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.reconcile.daybook import Statement, read_day
from warehouse.reconcile.totals import Totals


@dataclass(frozen=True, slots=True)
class StationDelta:
    station: str
    delta: Totals


@dataclass(frozen=True, slots=True)
class EditionComparison:
    before: Statement
    after: Statement
    station_deltas: tuple[StationDelta, ...]
    total_delta: Totals


def compare_days(db: Connection, before_id: int, after_id: int) -> EditionComparison:
    before = read_day(db, before_id)
    after = read_day(db, after_id)
    if before.day != after.day:
        raise ValueError('comparison requires editions of the same day')

    earlier = {station.station: station.totals for station in before.stations}
    later = {station.station: station.totals for station in after.stations}
    zero = Totals()
    deltas = tuple(
        StationDelta(station, later.get(station, zero) - earlier.get(station, zero))
        for station in sorted(earlier.keys() | later.keys())
    )
    return EditionComparison(before, after, deltas, after.totals - before.totals)
