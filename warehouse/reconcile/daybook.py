from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.publication.editions import EditionRow, load_edition
from warehouse.reconcile.stations import StationSummary, summarize_stations
from warehouse.reconcile.totals import Totals, summarize_readings


@dataclass(frozen=True, slots=True)
class Statement:
    edition_id: int
    day: str
    rows: tuple[EditionRow, ...]
    stations: tuple[StationSummary, ...]
    totals: Totals


def read_day(db: Connection, edition_id: int) -> Statement:
    edition = load_edition(db, edition_id)
    return Statement(
        edition.edition_id,
        edition.day,
        edition.rows,
        summarize_stations(edition.rows),
        summarize_readings(row.observation.liters for row in edition.rows),
    )
