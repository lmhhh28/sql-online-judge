from app.services.comparator import compare_csv_tables, compare_string, rows_to_csv


def test_unordered_csv_compare_accepts_different_row_order_and_numeric_formats():
    actual = "id,amount\n2,1.00\n1,2\n"
    expected = "id,amount\n1,2.0\n2,1\n"

    result = compare_csv_tables(actual, expected, ordered=False)

    assert result.accepted


def test_ordered_csv_compare_rejects_different_row_order():
    actual = "id\n2\n1\n"
    expected = "id\n1\n2\n"

    result = compare_csv_tables(actual, expected, ordered=True)

    assert not result.accepted


def test_csv_compare_supports_null_marker():
    actual = rows_to_csv(["name"], [[None]])
    expected = "name\nNULL\n"

    result = compare_csv_tables(actual, expected)

    assert result.accepted


def test_string_compare_is_exact_except_line_endings():
    assert compare_string("a\r\nb\n", "a\nb\n").accepted
    assert not compare_string("a\n", "a").accepted

