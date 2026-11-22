from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Origin:
    station: str
    batch_id: str
    line_no: int


@dataclass(frozen=True, slots=True)
class SourceObservation:
    label: str
    liters: int | None


def encode_reading(liters: int | None) -> str | None:
    # Hexadecimal text preserves all Python integers, including very large values.
    return None if liters is None else format(liters, 'x')


def decode_reading(encoded: str | None) -> int | None:
    return None if encoded is None else int(encoded, 16)
