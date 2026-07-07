import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


@dataclass(frozen=True)
class CompareResult:
    accepted: bool
    message: str


def _normalize_cell(value: str) -> Any:
    text = value.strip()
    if text.upper() == "NULL":
        return None
    try:
        return Decimal(text).normalize()
    except (InvalidOperation, ValueError):
        return text


def parse_csv_table(text: str) -> tuple[list[str], list[list[str]]]:
    stream = io.StringIO(text.replace("\r\n", "\n"))
    rows = list(csv.reader(stream))
    if not rows:
        return [], []
    header = rows[0]
    body = [row for row in rows[1:] if any(cell != "" for cell in row)]
    return header, body


def rows_to_csv(columns: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow(["NULL" if cell is None else cell for cell in row])
    return stream.getvalue()


def compare_csv_tables(actual: str, expected: str, ordered: bool = False) -> CompareResult:
    actual_header, actual_rows = parse_csv_table(actual)
    expected_header, expected_rows = parse_csv_table(expected)

    if actual_header != expected_header:
        return CompareResult(False, f"Column mismatch: got {actual_header}, expected {expected_header}")

    normalized_actual = [tuple(_normalize_cell(cell) for cell in row) for row in actual_rows]
    normalized_expected = [tuple(_normalize_cell(cell) for cell in row) for row in expected_rows]

    if len(normalized_actual) != len(normalized_expected):
        return CompareResult(False, f"Row count mismatch: got {len(normalized_actual)}, expected {len(normalized_expected)}")

    if ordered:
        accepted = normalized_actual == normalized_expected
    else:
        accepted = sorted(normalized_actual, key=repr) == sorted(normalized_expected, key=repr)

    if accepted:
        return CompareResult(True, "Accepted")

    preview_actual = normalized_actual[:5]
    preview_expected = normalized_expected[:5]
    return CompareResult(False, f"Result mismatch: got {preview_actual}, expected {preview_expected}")


def compare_string(actual: str, expected: str) -> CompareResult:
    normalized_actual = actual.replace("\r\n", "\n")
    normalized_expected = expected.replace("\r\n", "\n")
    if normalized_actual == normalized_expected:
        return CompareResult(True, "Accepted")
    return CompareResult(False, "String output mismatch")
