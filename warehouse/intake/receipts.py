from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class BatchReceipt:
    batch_id: str
    row_count: int
    token: str


def issue_batch_receipt(batch_id: str, row_count: int) -> BatchReceipt:
    return BatchReceipt(batch_id, row_count, uuid4().hex)
