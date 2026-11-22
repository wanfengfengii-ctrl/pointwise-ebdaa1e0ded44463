from dataclasses import dataclass
from sqlite3 import Connection

from warehouse.delivery.receipts import PageReceipt
from warehouse.intake.observations import Origin, SourceObservation, decode_reading, encode_reading
from warehouse.publication.editions import EditionRow


@dataclass(frozen=True, slots=True)
class SuccessfulPage:
    page_size: int
    rows: tuple[EditionRow, ...]
    receipt: PageReceipt

    @property
    def edition_id(self) -> int:
        return self.receipt.edition_id

    @property
    def page_no(self) -> int:
        return self.receipt.page_no

    @property
    def total_liters(self) -> int:
        return self.receipt.total_liters


def load_successful_page(db: Connection, session_id: str, page_no: int) -> SuccessfulPage | None:
    key = (session_id, page_no)
    header = db.execute(
        '''SELECT page_size, edition_id, liters, token FROM successful_pages
           WHERE session_id = ? AND page_no = ?''',
        key,
    ).fetchone()
    if header is None:
        return None
    page_size, edition_id, liters, token = header
    # The page's row references pin an immutable edition. Positions also retain its order.
    rows = tuple(
        EditionRow(position, SourceObservation(label, decode_reading(value)), Origin(station, batch, line))
        for position, label, value, station, batch, line in db.execute(
            '''SELECT row.position, row.label, row.liters, row.station, row.source_batch, row.source_position
               FROM delivered_rows AS delivery
               JOIN edition_rows AS row
                 ON row.edition_id = delivery.edition_id AND row.position = delivery.position
               WHERE delivery.session_id = ? AND delivery.page_no = ?
               ORDER BY delivery.position''',
            key,
        )
    )
    return SuccessfulPage(int(page_size, 16), rows, PageReceipt(session_id, page_no, edition_id, int(liters, 16), token))


def last_successful_page_no(db: Connection, session_id: str) -> int:
    (latest,) = db.execute(
        'SELECT COALESCE(MAX(page_no), 0) FROM successful_pages WHERE session_id = ?', (session_id,),
    ).fetchone()
    return latest


def store_successful_page(db: Connection, page: SuccessfulPage) -> None:
    receipt = page.receipt
    db.execute(
        '''INSERT INTO successful_pages (session_id, page_no, page_size, edition_id, liters, token)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (
            receipt.session_id, receipt.page_no, format(page.page_size, 'x'), receipt.edition_id,
            encode_reading(receipt.total_liters), receipt.token,
        ),
    )
    db.executemany(
        'INSERT INTO delivered_rows (session_id, page_no, edition_id, position) VALUES (?, ?, ?, ?)',
        ((receipt.session_id, receipt.page_no, receipt.edition_id, row.position) for row in page.rows),
    )


def read_delivery_evidence(db: Connection, session_id: str) -> dict[int, set[int]]:
    positions: dict[int, set[int]] = {}
    for edition_id, position in db.execute(
        'SELECT edition_id, position FROM delivered_rows WHERE session_id = ?', (session_id,),
    ):
        positions.setdefault(edition_id, set()).add(position)
    return positions
