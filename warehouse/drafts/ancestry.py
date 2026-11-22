from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DraftBase:
    day: str
    batch_ids: tuple[str, ...] = ()
    parent_id: int | None = None


def validate_draft_base(base: DraftBase, material_days: Iterable[str] = ()) -> None:
    if bool(base.batch_ids) == (base.parent_id is not None):
        raise ValueError('supply either received batches or one parent edition')
    if len(set(base.batch_ids)) != len(base.batch_ids):
        raise ValueError('each received batch may occur only once in an initial draft')
    if any(day != base.day for day in material_days):
        raise ValueError('all draft materials must belong to the draft day')
