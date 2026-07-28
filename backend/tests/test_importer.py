from __future__ import annotations

import zlib
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZIP_LZMA, ZipFile

import pytest

from app.services.importer import (
    MAX_ARCHIVE_MEMBERS,
    MAX_EXPECTED_COLUMNS,
    MAX_EXPECTED_OUTPUT_BYTES,
    MAX_EXPECTED_ROWS,
    MAX_MEMBER_SIZE,
    MAX_METADATA_SIZE,
    ImportErrorDetail,
    _parse_problem_package,
)


def make_package(files: dict[str, str | bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zip_file:
        for name, content in files.items():
            zip_file.writestr(name, content)
    return buffer.getvalue()


def valid_files(metadata: str | None = None) -> dict[str, str]:
    return {
        "problem.yaml": metadata
        or "slug: valid-problem\ntitle: Valid problem\ntags: [SELECT]\nordered: false\ntime_limit_ms: 3000\n",
        "statement.md": "Solve it",
        "tests/sample_1/judge.sql": "__USER_OUTPUT__\n",
        "tests/sample_1/expected.csv": "value\n1\n",
    }


def test_package_missing_metadata_is_rejected():
    from app.services.importer import import_problem_package

    with pytest.raises(ImportErrorDetail):
        import_problem_package(None, make_package({"statement.md": "x"}))  # type: ignore[arg-type]


def test_valid_package_is_parsed_without_database_writes():
    package = _parse_problem_package(make_package(valid_files()))

    assert package.slug == "valid-problem"
    assert package.tags == ["SELECT"]
    assert package.ordered is False
    assert package.time_limit_ms == 3000
    assert [case.name for case in package.test_cases] == ["sample_1"]
    assert package.test_cases[0].is_hidden is False


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ("- not-a-mapping\n", "mapping"),
        ("slug: x\ntitle: x\n", "slug"),
        ("slug: INVALID_slug\ntitle: x\n", "slug"),
        ("slug: valid\ntitle: x\ntags: SELECT\n", "tags"),
        ("slug: valid\ntitle: x\ntags: [SELECT, SELECT]\n", "duplicates"),
        ("slug: valid\ntitle: x\nordered: 'false'\n", "ordered"),
        ("slug: valid\ntitle: x\ntime_limit_ms: true\n", "time_limit_ms"),
        ("slug: valid\ntitle: x\ntime_limit_ms: 0\n", "time_limit_ms"),
        ("slug: valid\ntitle: x\ncase_score: .nan\n", "case_score"),
        ("slug: valid\ntitle: x\ncase_score: " + "9" * 400 + "\n", "case_score"),
        ("slug: valid\ntitle: 2024-99-99\n", "Invalid problem.yaml"),
        ('slug: valid\ntitle: "\\uD800"\n', "valid UTF-8"),
        ("slug: valid\ntitle: x\nordred: true\n", "unknown fields"),
        ("slug: valid\nslug: replaced\ntitle: x\n", "duplicate key"),
        ("slug: valid\ntitle: &title x\ndifficulty: *title\n", "aliases"),
    ],
)
def test_invalid_metadata_is_rejected(metadata: str, message: str):
    with pytest.raises(ImportErrorDetail, match=message):
        _parse_problem_package(make_package(valid_files(metadata)))


def test_non_utf8_required_file_is_rejected():
    files: dict[str, str | bytes] = valid_files()
    files["statement.md"] = b"\xff"

    with pytest.raises(ImportErrorDetail, match="UTF-8"):
        _parse_problem_package(make_package(files))


def test_nul_in_package_text_or_decoded_yaml_string_is_rejected():
    files = valid_files()
    files["statement.md"] = "bad\x00text"
    with pytest.raises(ImportErrorDetail, match="NUL"):
        _parse_problem_package(make_package(files))

    metadata = 'slug: valid\ntitle: "bad\\0text"\n'
    with pytest.raises(ImportErrorDetail, match="NUL"):
        _parse_problem_package(make_package(valid_files(metadata)))


def test_oversized_metadata_is_rejected_before_yaml_parsing():
    files = valid_files()
    files["problem.yaml"] = "x" * (MAX_METADATA_SIZE + 1)

    with pytest.raises(ImportErrorDetail, match="problem.yaml"):
        _parse_problem_package(make_package(files))


def test_duplicate_zip_entry_is_rejected():
    buffer = BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"):
        with ZipFile(buffer, "w") as zip_file:
            for name, content in valid_files().items():
                zip_file.writestr(name, content)
            zip_file.writestr("statement.md", "replacement")

    with pytest.raises(ImportErrorDetail, match="duplicate entry"):
        _parse_problem_package(buffer.getvalue())


def test_invalid_utf8_zip_filename_is_reported_as_an_import_error():
    package = bytearray(make_package(valid_files()))
    local_header = package.index(b"PK\x03\x04")
    central_header = package.index(b"PK\x01\x02")
    for flag_offset in (local_header + 6, central_header + 8):
        flags = int.from_bytes(package[flag_offset : flag_offset + 2], "little")
        package[flag_offset : flag_offset + 2] = (flags | 0x800).to_bytes(2, "little")
    package[local_header + 30] = 0xFF
    package[central_header + 46] = 0xFF

    with pytest.raises(ImportErrorDetail, match="valid zip"):
        _parse_problem_package(bytes(package))


def test_corrupt_zip_directory_metadata_is_reported_as_an_import_error():
    negative_seek = bytearray(make_package(valid_files()))
    end_record = negative_seek.rfind(b"PK\x05\x06")
    negative_seek[end_record + 12 : end_record + 16] = (0xFFFFFFFF).to_bytes(
        4, "little"
    )
    with pytest.raises(ImportErrorDetail, match="valid zip"):
        _parse_problem_package(bytes(negative_seek))

    unsupported_version = bytearray(make_package(valid_files()))
    central_header = unsupported_version.index(b"PK\x01\x02")
    unsupported_version[central_header + 6 : central_header + 8] = (200).to_bytes(
        2, "little"
    )
    with pytest.raises(ImportErrorDetail, match="valid zip"):
        _parse_problem_package(bytes(unsupported_version))


def test_corrupt_deflate_stream_is_reported_as_an_import_error():
    package = bytearray(make_package(valid_files()))
    with ZipFile(BytesIO(package)) as archive:
        member = archive.getinfo("tests/sample_1/judge.sql")
        compressed_start = (
            member.header_offset
            + 30
            + len(member.filename.encode("utf-8"))
            + len(member.extra)
        )
    package[compressed_start] ^= 0xFF

    with pytest.raises(ImportErrorDetail, match="Could not read") as caught:
        _parse_problem_package(bytes(package))
    assert isinstance(caught.value.__cause__, zlib.error)


def test_nul_in_original_zip_filename_is_rejected_before_zipfile_truncation():
    files = valid_files()
    files["extra.txt"] = "x"
    package = make_package(files).replace(b"extra.txt", b"extra.tx\x00")

    with pytest.raises(ImportErrorDetail, match="invalid entry name"):
        _parse_problem_package(package)


@pytest.mark.parametrize(
    "name",
    [
        "../problem.yaml",
        "/problem.yaml",
        "./problem.yaml",
        "tests//sample_1/judge.sql",
        r"tests\\sample_1\\judge.sql",
    ],
)
def test_unsafe_zip_entry_name_is_rejected(name: str):
    files = valid_files()
    files[name] = "x"

    with pytest.raises(ImportErrorDetail, match="entry name"):
        _parse_problem_package(make_package(files))


def test_zip_with_too_many_entries_is_rejected():
    files = valid_files()
    files.update({f"extra/{index}.txt": "" for index in range(MAX_ARCHIVE_MEMBERS)})

    with pytest.raises(ImportErrorDetail, match="too many entries"):
        _parse_problem_package(make_package(files))


def test_zip_bomb_sized_member_is_rejected_before_decompression():
    files = valid_files()
    files["extra.txt"] = "x" * (MAX_MEMBER_SIZE + 1)

    with pytest.raises(ImportErrorDetail, match="entry is too large"):
        _parse_problem_package(make_package(files))


def test_nonstandard_zip_compression_is_rejected_before_decompression():
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_LZMA) as archive:
        for name, content in valid_files().items():
            archive.writestr(name, content)

    with pytest.raises(ImportErrorDetail, match="stored or deflated"):
        _parse_problem_package(buffer.getvalue())


def test_invalid_or_empty_test_case_is_rejected():
    files = valid_files()
    files["tests/bad name/judge.sql"] = "select 1"
    files["tests/bad name/expected.csv"] = "x\n1\n"

    with pytest.raises(ImportErrorDetail, match="Invalid test case"):
        _parse_problem_package(make_package(files))

    files = valid_files()
    files["tests/sample_1/judge.sql"] = " \n"
    with pytest.raises(ImportErrorDetail, match="must not be empty"):
        _parse_problem_package(make_package(files))


@pytest.mark.parametrize(
    ("expected", "message"),
    [
        ("", "header row"),
        ('value\n"unterminated\n', "Invalid expected CSV"),
        ("a,b\n1\n", "row 2"),
        (
            ",".join(f"c{index}" for index in range(MAX_EXPECTED_COLUMNS + 1)) + "\n",
            "too many columns",
        ),
        ("value\n" + "1\n" * (MAX_EXPECTED_ROWS + 1), "too many rows"),
        ("value\n" + "x" * MAX_EXPECTED_OUTPUT_BYTES, "too large"),
    ],
)
def test_invalid_or_unjudgeable_expected_csv_is_rejected(expected: str, message: str):
    files = valid_files()
    files["tests/sample_1/expected.csv"] = expected

    with pytest.raises(ImportErrorDetail, match=message):
        _parse_problem_package(make_package(files))


def test_string_checker_still_requires_possible_csv_output():
    files = valid_files("slug: valid\ntitle: x\nchecker: string\n")
    files["tests/sample_1/expected.csv"] = "not,a,rectangular,csv\njust text"

    with pytest.raises(ImportErrorDetail, match="row 2"):
        _parse_problem_package(make_package(files))
