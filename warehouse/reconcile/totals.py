from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Totals:
    rows: int = 0
    missing: int = 0
    liters: int = 0

    def __sub__(self, earlier: 'Totals') -> 'Totals':
        return Totals(self.rows - earlier.rows, self.missing - earlier.missing, self.liters - earlier.liters)


def summarize_readings(readings: Iterable[int | None]) -> Totals:
    rows = 0
    missing = 0
    liters = 0
    for reading in readings:
        rows += 1
        if reading is None:
            missing += 1
        else:
            liters += reading
    return Totals(rows, missing, liters)
