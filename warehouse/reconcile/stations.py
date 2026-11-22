from collections.abc import Iterable
from dataclasses import dataclass

from warehouse.publication.editions import EditionRow
from warehouse.reconcile.totals import Totals, summarize_readings


@dataclass(frozen=True, slots=True)
class StationSummary:
    station: str
    totals: Totals


def summarize_stations(rows: Iterable[EditionRow]) -> tuple[StationSummary, ...]:
    by_station: dict[str, list[int | None]] = {}
    for row in rows:
        by_station.setdefault(row.origin.station, []).append(row.observation.liters)
    return tuple(
        StationSummary(station, summarize_readings(by_station[station]))
        for station in sorted(by_station)
    )
