from app.services.comparator import (
    MAX_CSV_FIELD_CHARS,
    NULL_MARKER,
    PREVIEW_CELL_CHARS,
    PREVIEW_COLUMNS,
    compare_csv_tables,
    compare_string,
    parse_csv_table,
    rows_to_csv,
)


def test_unordered_csv_compare_accepts_different_row_order():
    actual = "id,amount\n2,1\n1,2\n"
    expected = "id,amount\n1,2\n2,1\n"

    result = compare_csv_tables(actual, expected, ordered=False)

    assert result.accepted


def test_csv_compare_keeps_numeric_looking_strings_distinct():
    actual = "student_id\n7\n"
    expected = "student_id\n007\n"

    result = compare_csv_tables(actual, expected, ordered=False)

    assert not result.accepted


def test_ordered_csv_compare_rejects_different_row_order():
    actual = "id\n2\n1\n"
    expected = "id\n1\n2\n"

    result = compare_csv_tables(actual, expected, ordered=True)

    assert not result.accepted


def test_csv_compare_supports_null_marker():
    actual = rows_to_csv(["name"], [[None]])
    expected = f"name\n{NULL_MARKER}\n"

    result = compare_csv_tables(actual, expected)

    assert result.accepted


def test_csv_compare_distinguishes_null_from_literal_null_string():
    actual = rows_to_csv(["name"], [[None], ["NULL"]])
    expected = f"name\n{NULL_MARKER}\nNULL\n"

    result = compare_csv_tables(actual, expected)

    assert result.accepted
    assert not compare_csv_tables(
        rows_to_csv(["name"], [[None]]), "name\nNULL\n"
    ).accepted


def test_string_compare_is_exact_except_line_endings():
    assert compare_string("a\r\nb\n", "a\nb\n").accepted
    assert not compare_string("a\n", "a").accepted


def test_csv_compare_preserves_explicit_empty_string_rows():
    actual = rows_to_csv(["value"], [[""]])

    header, rows = parse_csv_table(actual)

    assert header == ["value"]
    assert rows == [[""]]
    assert compare_csv_tables(actual, 'value\n""\n').accepted


def test_csv_compare_ignores_only_physically_blank_lines():
    assert compare_csv_tables("value\n\n1\n", "value\n1\n").accepted


def test_csv_compare_rejects_ragged_rows():
    result = compare_csv_tables("a,b\n1\n", "a,b\n1,2\n")

    assert not result.accepted
    assert "row 2 has 1 columns" in result.message


def test_csv_compare_keeps_column_names_and_order_exact():
    assert not compare_csv_tables("a,b\n1,2\n", "b,a\n1,2\n").accepted
    assert not compare_csv_tables("A\n1\n", "a\n1\n").accepted


def test_csv_compare_keeps_float_rendering_exact():
    assert compare_csv_tables("value\n1.0\n", "value\n1.0\n").accepted
    assert not compare_csv_tables("value\n1.0\n", "value\n1.00\n").accepted


def test_csv_compare_preserves_duplicate_rows_when_unordered():
    assert not compare_csv_tables("x\n1\n1\n", "x\n1\n", ordered=False).accepted


def test_csv_compare_handles_fields_larger_than_csv_default_limit():
    value = "x" * 150_000

    assert len(value) < MAX_CSV_FIELD_CHARS
    assert compare_csv_tables(
        rows_to_csv(["value"], [[value]]), rows_to_csv(["value"], [[value]])
    ).accepted


def test_csv_compare_reports_malformed_csv_without_raising():
    result = compare_csv_tables('value\n"unterminated', "value\nok\n")

    assert not result.accepted
    assert result.message == "Invalid actual CSV output"


def test_csv_mismatch_preview_is_bounded():
    result = compare_csv_tables(
        rows_to_csv(["value"], [["a" * 1000]]), rows_to_csv(["value"], [["b"]])
    )

    assert not result.accepted
    assert len(result.message) < PREVIEW_CELL_CHARS + 100


def test_csv_mismatch_preview_bounds_wide_results():
    columns = [f"c{index}" for index in range(256)]
    actual = rows_to_csv(columns, [["a" * 1000] * len(columns)] * 5)
    expected = rows_to_csv(columns, [["b"] * len(columns)] * 5)

    result = compare_csv_tables(actual, expected)

    assert not result.accepted
    assert "..." in result.message
    assert len(result.message) < (PREVIEW_CELL_CHARS + 10) * PREVIEW_COLUMNS * 5 + 200
