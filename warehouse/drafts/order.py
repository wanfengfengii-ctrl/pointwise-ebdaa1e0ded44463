from warehouse.drafts.store import DraftRow


def row_index(position: int, last_position: int) -> int:
    if type(position) is not int or not 1 <= position <= last_position:
        raise ValueError(f'position must be between 1 and {last_position}')
    return position - 1


def insert_row(rows: list[DraftRow], position: int, row: DraftRow) -> None:
    rows.insert(row_index(position, len(rows) + 1), row)


def remove_row(rows: list[DraftRow], position: int) -> None:
    del rows[row_index(position, len(rows))]


def move_row(rows: list[DraftRow], position: int, destination: int) -> None:
    source_index = row_index(position, len(rows))
    final_index = row_index(destination, len(rows))
    rows.insert(final_index, rows.pop(source_index))
