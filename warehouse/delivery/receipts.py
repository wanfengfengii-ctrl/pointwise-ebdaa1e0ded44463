from dataclasses import dataclass
from uuid import uuid4

from warehouse.publication.editions import EditionRow
from warehouse.reconcile.totals import summarize_readings


@dataclass(frozen=True, slots=True)
class PageReceipt:
    session_id: str
    page_no: int
    edition_id: int
    total_liters: int
    token: str


def issue_page_receipt(
    session_id: str, page_no: int, edition_id: int, rows: tuple[EditionRow, ...],
) -> PageReceipt:
    totals = summarize_readings(row.observation.liters for row in rows)
    return PageReceipt(session_id, page_no, edition_id, totals.liters, uuid4().hex)
