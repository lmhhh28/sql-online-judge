from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import Any, Iterable

NULL_MARKER = r"\N"
PREVIEW_ROWS = 5
PREVIEW_COLUMNS = 8
PREVIEW_CELL_CHARS = 120
MAX_CSV_FIELD_CHARS = 1_000_000

# csv's process-wide default is only about 128 KiB, smaller than the judge's
# bounded output allowance. Set one explicit ceiling during module import.
csv.field_size_limit(MAX_CSV_FIELD_CHARS)


@dataclass(frozen=True)
class CompareResult:
    accepted: bool
    message: str


def _normalize_cell(value: str) -> Any:
    if value == NULL_MARKER:
        return None
    return value


def parse_csv_table(text: str) -> tuple[list[str], list[list[str]]]:
    stream = io.StringIO(text.replace("\r\n", "\n"))
    rows = list(csv.reader(stream, strict=True))
    if not rows:
        return [], []
    header = rows[0]
    # csv.reader represents a physically blank line as [], while an explicit
    # empty-string record ("\"\"") is [""]. Only the former is ignorable.
    body = [row for row in rows[1:] if row]
    return header, body


def rows_to_csv(columns: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(list(columns))
    for row in rows:
        writer.writerow([NULL_MARKER if cell is None else cell for cell in row])
    return stream.getvalue()


def _short_repr(value: Any) -> str:
    rendered = repr(value)
    if len(rendered) <= PREVIEW_CELL_CHARS:
        return rendered
    return f"{rendered[:PREVIEW_CELL_CHARS]}..."


def _preview(rows: list[tuple[Any, ...]]) -> str:
    rendered_rows = []
    for row in rows[:PREVIEW_ROWS]:
        cells = ", ".join(_short_repr(cell) for cell in row[:PREVIEW_COLUMNS])
        column_suffix = ", ..." if len(row) > PREVIEW_COLUMNS else ""
        rendered_rows.append("(" + cells + column_suffix + ")")
    suffix = ", ..." if len(rows) > PREVIEW_ROWS else ""
    return "[" + ", ".join(rendered_rows) + suffix + "]"


def _row_width_error(
    label: str, header: list[str], rows: list[list[str]]
) -> CompareResult | None:
    expected_width = len(header)
    for row_number, row in enumerate(rows, start=2):
        if len(row) != expected_width:
            return CompareResult(
                False,
                f"Invalid {label} CSV: row {row_number} has {len(row)} columns; expected {expected_width}",
            )
    return None


def compare_csv_tables(
    actual: str, expected: str, ordered: bool = False
) -> CompareResult:
    try:
        actual_header, actual_rows = parse_csv_table(actual)
    except (csv.Error, UnicodeError):
        return CompareResult(False, "Invalid actual CSV output")
    try:
        expected_header, expected_rows = parse_csv_table(expected)
    except (csv.Error, UnicodeError):
        return CompareResult(False, "Invalid expected CSV output")

    if actual_header != expected_header:
        return CompareResult(
            False,
            f"Column mismatch: got {_short_repr(actual_header)}, expected {_short_repr(expected_header)}",
        )

    width_error = _row_width_error("actual", actual_header, actual_rows)
    if width_error is not None:
        return width_error
    width_error = _row_width_error("expected", expected_header, expected_rows)
    if width_error is not None:
        return width_error

    normalized_actual = [
        tuple(_normalize_cell(cell) for cell in row) for row in actual_rows
    ]
    normalized_expected = [
        tuple(_normalize_cell(cell) for cell in row) for row in expected_rows
    ]

    if len(normalized_actual) != len(normalized_expected):
        return CompareResult(
            False,
            f"Row count mismatch: got {len(normalized_actual)}, expected {len(normalized_expected)}",
        )

    if ordered:
        accepted = normalized_actual == normalized_expected
    else:
        accepted = sorted(normalized_actual, key=repr) == sorted(
            normalized_expected, key=repr
        )

    if accepted:
        return CompareResult(True, "Accepted")

    return CompareResult(
        False,
        f"Result mismatch: got {_preview(normalized_actual)}, expected {_preview(normalized_expected)}",
    )


def compare_string(actual: str, expected: str) -> CompareResult:
    normalized_actual = actual.replace("\r\n", "\n")
    normalized_expected = expected.replace("\r\n", "\n")
    if normalized_actual == normalized_expected:
        return CompareResult(True, "Accepted")
    return CompareResult(False, "String output mismatch")
