from __future__ import annotations

import csv
import math
import re
import zlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    LargeZipFile,
    ZipFile,
    ZipInfo,
)

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from yaml.constructor import ConstructorError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from app.models import Problem, Submission, TestCase, utc_now
from app.services.comparator import parse_csv_table

MAX_ARCHIVE_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256
MAX_MEMBER_SIZE = 2 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 16 * 1024 * 1024
MAX_METADATA_SIZE = 64 * 1024
MAX_TEST_CASES = 100
MAX_TAGS = 32
MAX_TAG_LENGTH = 64
MAX_PROBLEM_TEXT_LENGTH = 1_000_000
MAX_EXPECTED_OUTPUT_BYTES = 1_000_000
MAX_EXPECTED_ROWS = 10_000
MAX_EXPECTED_COLUMNS = 256
MAX_CASE_SCORE = 1_000_000.0
MAX_TIME_LIMIT_MS = 30_000
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
TEST_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
PROBLEM_METADATA_KEYS = {
    "slug",
    "title",
    "difficulty",
    "tags",
    "checker",
    "ordered",
    "time_limit_ms",
    "is_active",
    "case_score",
}


class ImportErrorDetail(ValueError):
    pass


class ImportConflictError(ImportErrorDetail):
    pass


class _PackageMetadataLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects aliases and duplicate mapping keys."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(AliasEvent):
            event = self.peek_event()
            raise ConstructorError(
                None,
                None,
                "YAML aliases are not supported",
                event.start_mark,
            )
        return super().compose_node(parent, index)


def _construct_unique_mapping(
    loader: _PackageMetadataLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_PackageMetadataLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _validate_metadata_utf8(metadata: Any) -> None:
    pending = [metadata]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            if "\x00" in value:
                raise ImportErrorDetail(
                    "problem.yaml strings must not contain NUL characters"
                )
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ImportErrorDetail(
                    "problem.yaml strings must contain valid UTF-8 characters"
                ) from exc
        elif isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (list, tuple, set)):
            pending.extend(value)


@dataclass(frozen=True)
class ImportSummary:
    problem: Problem
    imported_cases: int


@dataclass(frozen=True)
class ParsedTestCase:
    name: str
    is_hidden: bool
    score: float
    setup_sql: str
    judge_sql: str
    expected_output: str


@dataclass(frozen=True)
class ParsedPackage:
    slug: str
    title: str
    statement_md: str
    schema_md: str
    difficulty: str
    tags: list[str]
    checker: str
    ordered: bool
    time_limit_ms: int
    is_active: bool
    test_cases: list[ParsedTestCase]


def _validate_archive(package_bytes: bytes, zip_file: ZipFile) -> None:
    if len(package_bytes) > MAX_ARCHIVE_SIZE:
        raise ImportErrorDetail(
            f"Zip file is too large (maximum {MAX_ARCHIVE_SIZE // (1024 * 1024)} MiB)"
        )

    members = zip_file.infolist()
    if len(members) > MAX_ARCHIVE_MEMBERS:
        raise ImportErrorDetail(
            f"Zip contains too many entries (maximum {MAX_ARCHIVE_MEMBERS})"
        )

    seen_names: set[str] = set()
    total_size = 0
    for member in members:
        _validate_member_name(member)
        if member.filename in seen_names:
            raise ImportErrorDetail(
                f"Zip contains a duplicate entry: {member.filename}"
            )
        seen_names.add(member.filename)

        if member.flag_bits & 0x1:
            raise ImportErrorDetail(
                f"Encrypted zip entries are not supported: {member.filename}"
            )
        if member.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
            raise ImportErrorDetail(
                "Zip entries must use stored or deflated compression: "
                f"{member.filename}"
            )
        if member.file_size > MAX_MEMBER_SIZE:
            raise ImportErrorDetail(
                f"Zip entry is too large: {member.filename} (maximum {MAX_MEMBER_SIZE // (1024 * 1024)} MiB)"
            )
        total_size += member.file_size
        if total_size > MAX_UNCOMPRESSED_SIZE:
            raise ImportErrorDetail(
                f"Zip expands to too much data (maximum {MAX_UNCOMPRESSED_SIZE // (1024 * 1024)} MiB)"
            )


def _validate_member_name(member: ZipInfo) -> None:
    # zipfile truncates ``filename`` at a NUL while retaining the original
    # central-directory value in ``orig_filename``. Validate the unmodified
    # name so an archive cannot hide forbidden bytes behind that normalization.
    name = member.orig_filename
    if not name or "\x00" in name or "\\" in name:
        raise ImportErrorDetail("Zip contains an invalid entry name")
    raw_parts = name.split("/")
    if member.is_dir():
        raw_parts = raw_parts[:-1]
    if not raw_parts or any(part in {"", ".", ".."} for part in raw_parts):
        raise ImportErrorDetail(f"Zip contains an unsafe entry name: {name}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ImportErrorDetail(f"Zip contains an unsafe entry name: {name}")


def _read_text(
    zip_file: ZipFile, path: str, required: bool = True, max_size: int | None = None
) -> str:
    try:
        member = zip_file.getinfo(path)
        if max_size is not None and member.file_size > max_size:
            raise ImportErrorDetail(
                f"File is too large: {path} (maximum {max_size} bytes)"
            )
        with zip_file.open(path) as file:
            text = file.read().decode("utf-8")
        if "\x00" in text:
            raise ImportErrorDetail(f"File must not contain NUL characters: {path}")
        return text
    except ImportErrorDetail:
        raise
    except KeyError:
        if required:
            raise ImportErrorDetail(f"Missing required file: {path}") from None
        return ""
    except UnicodeDecodeError as exc:
        raise ImportErrorDetail(f"File must be valid UTF-8: {path}") from exc
    except (
        BadZipFile,
        LargeZipFile,
        EOFError,
        NotImplementedError,
        OverflowError,
        RuntimeError,
        UnicodeError,
        ValueError,
        zlib.error,
        OSError,
    ) as exc:
        raise ImportErrorDetail(f"Could not read zip entry: {path}") from exc


def _list_test_dirs(zip_file: ZipFile) -> list[str]:
    dirs: set[str] = set()
    for member in zip_file.infolist():
        if member.is_dir():
            continue
        path = PurePosixPath(member.filename)
        if len(path.parts) >= 3 and path.parts[0] == "tests":
            dirs.add("/".join(path.parts[:2]))
    return sorted(dirs)


def _required_string(metadata: dict[str, Any], key: str, *, max_length: int) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ImportErrorDetail(f"problem.yaml must include a non-empty string {key}")
    value = value.strip()
    if len(value) > max_length:
        raise ImportErrorDetail(f"{key} must be at most {max_length} characters")
    return value


def _optional_string(
    metadata: dict[str, Any], key: str, default: str, *, max_length: int
) -> str:
    value = metadata.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ImportErrorDetail(f"{key} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ImportErrorDetail(f"{key} must be at most {max_length} characters")
    return value


def _boolean(metadata: dict[str, Any], key: str, default: bool) -> bool:
    value = metadata.get(key, default)
    if type(value) is not bool:
        raise ImportErrorDetail(f"{key} must be true or false")
    return value


def _integer(
    metadata: dict[str, Any], key: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = metadata.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ImportErrorDetail(
            f"{key} must be an integer between {minimum} and {maximum}"
        )
    return value


def _score(metadata: dict[str, Any]) -> float:
    value = metadata.get("case_score", 1.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImportErrorDetail("case_score must be a number")
    try:
        score = float(value)
    except (OverflowError, ValueError) as exc:
        raise ImportErrorDetail("case_score must be a finite number") from exc
    if not math.isfinite(score) or score < 0 or score > MAX_CASE_SCORE:
        raise ImportErrorDetail(
            f"case_score must be finite and between 0 and {MAX_CASE_SCORE:g}"
        )
    return score


def _tags(metadata: dict[str, Any]) -> list[str]:
    value = metadata.get("tags", [])
    if not isinstance(value, list):
        raise ImportErrorDetail("tags must be a list of strings")
    if len(value) > MAX_TAGS:
        raise ImportErrorDetail(f"tags may contain at most {MAX_TAGS} values")

    tags: list[str] = []
    for tag in value:
        if not isinstance(tag, str) or not tag.strip():
            raise ImportErrorDetail("tags must contain only non-empty strings")
        tag = tag.strip()
        if len(tag) > MAX_TAG_LENGTH:
            raise ImportErrorDetail(
                f"each tag must be at most {MAX_TAG_LENGTH} characters"
            )
        tags.append(tag)
    if len(set(tags)) != len(tags):
        raise ImportErrorDetail("tags must not contain duplicates")
    return tags


def validate_expected_output(text: str, checker: str, path: str) -> None:
    if len(text.encode("utf-8")) > MAX_EXPECTED_OUTPUT_BYTES:
        raise ImportErrorDetail(
            f"Expected output is too large: {path} (maximum {MAX_EXPECTED_OUTPUT_BYTES} bytes)"
        )
    try:
        header, rows = parse_csv_table(text)
    except (csv.Error, UnicodeError) as exc:
        raise ImportErrorDetail(f"Invalid expected CSV: {path}") from exc
    if not header:
        raise ImportErrorDetail(f"Expected CSV must include a header row: {path}")
    if len(header) > MAX_EXPECTED_COLUMNS:
        raise ImportErrorDetail(
            f"Expected CSV has too many columns: {path} (maximum {MAX_EXPECTED_COLUMNS})"
        )
    if len(rows) > MAX_EXPECTED_ROWS:
        raise ImportErrorDetail(
            f"Expected CSV has too many rows: {path} (maximum {MAX_EXPECTED_ROWS})"
        )
    for row_number, row in enumerate(rows, start=2):
        if len(row) != len(header):
            raise ImportErrorDetail(
                f"Invalid expected CSV: {path} row {row_number} has {len(row)} columns; "
                f"expected {len(header)}"
            )


def _parse_problem_package(package_bytes: bytes) -> ParsedPackage:
    if not isinstance(package_bytes, bytes):
        raise ImportErrorDetail("Uploaded package must be binary zip data")
    try:
        zip_file = ZipFile(BytesIO(package_bytes))
    except (
        BadZipFile,
        LargeZipFile,
        EOFError,
        NotImplementedError,
        OverflowError,
        RuntimeError,
        UnicodeError,
        ValueError,
        zlib.error,
        OSError,
    ) as exc:
        raise ImportErrorDetail("Uploaded file is not a valid zip") from exc

    with zip_file:
        _validate_archive(package_bytes, zip_file)
        metadata_text = _read_text(zip_file, "problem.yaml", max_size=MAX_METADATA_SIZE)
        loader = _PackageMetadataLoader(metadata_text)
        try:
            metadata = loader.get_single_data() or {}
        except (yaml.YAMLError, RecursionError, OverflowError, ValueError) as exc:
            raise ImportErrorDetail(f"Invalid problem.yaml: {exc}") from exc
        finally:
            loader.dispose()
        _validate_metadata_utf8(metadata)
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) for key in metadata
        ):
            raise ImportErrorDetail(
                "problem.yaml must contain a mapping with string keys"
            )
        unknown_keys = sorted(set(metadata) - PROBLEM_METADATA_KEYS)
        if unknown_keys:
            raise ImportErrorDetail(
                f"problem.yaml contains unknown fields: {', '.join(unknown_keys)}"
            )

        slug = _required_string(metadata, "slug", max_length=80)
        if len(slug) < 2 or SLUG_PATTERN.fullmatch(slug) is None:
            raise ImportErrorDetail(
                "slug must contain lowercase letters, numbers, and single hyphens only"
            )
        title = _required_string(metadata, "title", max_length=200)
        difficulty = _optional_string(metadata, "difficulty", "入门", max_length=32)
        checker = _optional_string(metadata, "checker", "result", max_length=24)
        if checker not in {"result", "string"}:
            raise ImportErrorDetail("checker must be result or string")

        statement_md = _read_text(zip_file, "statement.md")
        schema_md = _read_text(zip_file, "schema.md", required=False)
        if any(
            len(text) > MAX_PROBLEM_TEXT_LENGTH
            or len(text.encode("utf-8")) > MAX_PROBLEM_TEXT_LENGTH
            for text in (statement_md, schema_md)
        ):
            raise ImportErrorDetail(
                "statement.md and schema.md must each be at most "
                f"{MAX_PROBLEM_TEXT_LENGTH} characters and UTF-8 bytes"
            )
        score = _score(metadata)

        test_dirs = _list_test_dirs(zip_file)
        if not test_dirs:
            raise ImportErrorDetail(
                "Package must include at least one test case under tests/"
            )
        if len(test_dirs) > MAX_TEST_CASES:
            raise ImportErrorDetail(
                f"Package contains too many test cases (maximum {MAX_TEST_CASES})"
            )

        test_cases: list[ParsedTestCase] = []
        for test_dir in test_dirs:
            name = test_dir.split("/", 1)[1]
            if len(name) > 120 or TEST_NAME_PATTERN.fullmatch(name) is None:
                raise ImportErrorDetail(f"Invalid test case directory name: {name}")
            setup_sql = _read_text(zip_file, f"{test_dir}/setup.sql", required=False)
            judge_sql = _read_text(zip_file, f"{test_dir}/judge.sql")
            expected_output = _read_text(zip_file, f"{test_dir}/expected.csv")
            if not judge_sql.strip():
                raise ImportErrorDetail(
                    f"judge.sql must not be empty: {test_dir}/judge.sql"
                )
            validate_expected_output(
                expected_output, checker, f"{test_dir}/expected.csv"
            )
            test_cases.append(
                ParsedTestCase(
                    name=name,
                    is_hidden=not name.startswith("sample_"),
                    score=score,
                    setup_sql=setup_sql,
                    judge_sql=judge_sql,
                    expected_output=expected_output,
                )
            )

        return ParsedPackage(
            slug=slug,
            title=title,
            statement_md=statement_md,
            schema_md=schema_md,
            difficulty=difficulty,
            tags=_tags(metadata),
            checker=checker,
            ordered=_boolean(metadata, "ordered", False),
            time_limit_ms=_integer(
                metadata,
                "time_limit_ms",
                3000,
                minimum=100,
                maximum=MAX_TIME_LIMIT_MS,
            ),
            is_active=_boolean(metadata, "is_active", True),
            test_cases=test_cases,
        )


def import_problem_package(db: Session, package_bytes: bytes) -> ImportSummary:
    package = _parse_problem_package(package_bytes)

    try:
        problem = db.scalar(
            select(Problem).where(Problem.slug == package.slug).with_for_update()
        )
        if problem is None:
            problem = Problem(slug=package.slug, title=package.title)
            db.add(problem)
        else:
            active_submission = db.scalar(
                select(Submission.id)
                .where(
                    Submission.problem_id == problem.id,
                    Submission.status.in_(("PENDING", "RUNNING")),
                )
                .limit(1)
            )
            if active_submission is not None:
                raise ImportConflictError(
                    "Problem cannot be re-imported while a submission is pending or running"
                )
            # Delete in SQL so re-importing a legacy problem does not first
            # materialize every old setup/query/expected-output value in the
            # application process. The database preserves historical results
            # by setting their test_case_id foreign key to NULL.
            db.execute(
                delete(TestCase)
                .where(TestCase.problem_id == problem.id)
                .execution_options(synchronize_session=False)
            )

        problem.title = package.title
        problem.statement_md = package.statement_md
        problem.schema_md = package.schema_md
        problem.difficulty = package.difficulty
        problem.tags = package.tags
        problem.checker = package.checker
        problem.ordered = package.ordered
        problem.time_limit_ms = package.time_limit_ms
        problem.is_active = package.is_active
        problem.updated_at = utc_now()
        db.flush()
        db.add_all(
            TestCase(
                problem_id=problem.id,
                name=case.name,
                is_hidden=case.is_hidden,
                score=case.score,
                setup_sql=case.setup_sql,
                judge_sql=case.judge_sql,
                expected_output=case.expected_output,
            )
            for case in package.test_cases
        )

        db.commit()
        db.refresh(problem)
        return ImportSummary(problem=problem, imported_cases=len(package.test_cases))
    except Exception:
        db.rollback()
        raise
