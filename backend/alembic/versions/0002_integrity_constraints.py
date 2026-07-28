"""normalize legacy data, then add integrity constraints and query indexes

Revision ID: 0002_integrity_constraints
Revises: 0001_initial
Create Date: 2026-07-28
"""

import csv
import io
import json
import re
import unicodedata
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0002_integrity_constraints"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROUTABLE_LEGACY_SLUG = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MAX_EXPECTED_BYTES = 1_000_000
_MAX_EXPECTED_ROWS = 10_000
_MAX_EXPECTED_COLUMNS = 256
_MAX_CASE_FILE_BYTES = 2 * 1024 * 1024
_MAX_CASE_CONTENT_BYTES = 16 * 1024 * 1024


def _normalized_slug(value: str, problem_id: int) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    candidate = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    candidate = re.sub(r"-+", "-", candidate)
    if len(candidate) < 2:
        candidate = f"problem-{problem_id}"
    return candidate[:80].rstrip("-")


def _unique_slug(base: str, problem_id: int, used: set[str]) -> str:
    candidate = base
    counter = 0
    while candidate in used:
        counter += 1
        suffix = f"-{problem_id}" if counter == 1 else f"-{problem_id}-{counter}"
        prefix = base[: 80 - len(suffix)].rstrip("-") or "problem"
        candidate = f"{prefix}{suffix}"
    used.add(candidate)
    return candidate


def _normalize_unroutable_slugs() -> None:
    connection = op.get_bind()
    rows = list(
        connection.execute(sa.text("SELECT id, slug FROM problems ORDER BY id"))
    )
    used = {slug for _, slug in rows if _ROUTABLE_LEGACY_SLUG.fullmatch(slug)}
    replacements: list[tuple[int, str]] = []
    for problem_id, old_slug in rows:
        if _ROUTABLE_LEGACY_SLUG.fullmatch(old_slug):
            continue
        replacements.append(
            (
                problem_id,
                _unique_slug(_normalized_slug(old_slug, problem_id), problem_id, used),
            )
        )

    # Move affected rows through collision-free placeholders because a final
    # slug may currently be owned by another affected row.
    occupied = {slug for _, slug in rows} | {slug for _, slug in replacements}
    placeholders: dict[int, str] = {}
    for problem_id, _ in replacements:
        placeholder = f"migration-placeholder-{problem_id}"
        counter = 0
        while placeholder in occupied:
            counter += 1
            placeholder = f"migration-placeholder-{problem_id}-{counter}"
        occupied.add(placeholder)
        placeholders[problem_id] = placeholder
        connection.execute(
            sa.text("UPDATE problems SET slug = :slug WHERE id = :problem_id"),
            {"slug": placeholder, "problem_id": problem_id},
        )

    for problem_id, final_slug in replacements:
        connection.execute(
            sa.text("UPDATE problems SET slug = :slug WHERE id = :problem_id"),
            {"slug": final_slug, "problem_id": problem_id},
        )


def _expected_csv_is_valid(text: str) -> bool:
    if len(text.encode("utf-8")) > _MAX_EXPECTED_BYTES:
        return False
    try:
        reader = csv.reader(io.StringIO(text.replace("\r\n", "\n")), strict=True)
        header = next(reader, None)
        if not header or len(header) > _MAX_EXPECTED_COLUMNS:
            return False
        row_count = 0
        for row in reader:
            if not row:
                continue
            row_count += 1
            if row_count > _MAX_EXPECTED_ROWS or len(row) != len(header):
                return False
    except (csv.Error, UnicodeError):
        return False
    return True


def _normalize_legacy_tags() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, tags FROM problems ORDER BY id"))
    for problem_id, tags in rows:
        normalized: list[str] = []
        seen: set[str] = set()
        if isinstance(tags, list):
            for value in tags:
                if not isinstance(value, str):
                    continue
                tag = value.strip()[:64]
                if not tag or tag in seen:
                    continue
                normalized.append(tag)
                seen.add(tag)
                if len(normalized) == 32:
                    break
        connection.execute(
            sa.text(
                "UPDATE problems SET tags = CAST(:tags AS jsonb) WHERE id = :problem_id"
            ),
            {
                "tags": json.dumps(normalized, ensure_ascii=False),
                "problem_id": problem_id,
            },
        )


def _deactivate_unsafe_legacy_problems() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
        UPDATE problems AS problem
        SET is_active = false
        WHERE problem.is_active
          AND (
              char_length(problem.statement_md) > 1000000
              OR octet_length(problem.statement_md) > 1000000
              OR char_length(problem.schema_md) > 1000000
              OR octet_length(problem.schema_md) > 1000000
              OR (
                  SELECT count(*) FROM test_cases AS test
                  WHERE test.problem_id = problem.id
              ) = 0
              OR (
                  SELECT count(*) FROM test_cases AS test
                  WHERE test.problem_id = problem.id
              ) > 100
              OR EXISTS (
                  SELECT 1 FROM test_cases AS test
                  WHERE test.problem_id = problem.id
                    AND (
                        octet_length(test.setup_sql) > :max_case_file_bytes
                        OR octet_length(test.judge_sql) > :max_case_file_bytes
                        OR octet_length(test.expected_output) > :max_expected_bytes
                        OR test.judge_sql !~ '[^[:space:]]'
                    )
              )
              OR COALESCE((
                  SELECT sum(
                      octet_length(test.setup_sql)
                      + octet_length(test.judge_sql)
                      + octet_length(test.expected_output)
                  )
                  FROM test_cases AS test
                  WHERE test.problem_id = problem.id
              ), 0) > :max_case_content_bytes
              OR COALESCE((
                  SELECT sum(test.score)
                  FROM test_cases AS test
                  WHERE test.problem_id = problem.id
              ), 0) > 100000000
          )
        """
        ),
        {
            "max_case_file_bytes": _MAX_CASE_FILE_BYTES,
            "max_expected_bytes": _MAX_EXPECTED_BYTES,
            "max_case_content_bytes": _MAX_CASE_CONTENT_BYTES,
        },
    )

    invalid_problem_ids: set[int] = set()
    rows = connection.execute(
        sa.text(
            """
            SELECT problem.id, test.expected_output, test.judge_sql
            FROM problems AS problem
            JOIN test_cases AS test ON test.problem_id = problem.id
            WHERE problem.is_active
            ORDER BY problem.id, test.id
            """
        )
    )
    previous_csv_limit = csv.field_size_limit()
    csv.field_size_limit(_MAX_EXPECTED_BYTES)
    try:
        for problem_id, expected_output, judge_sql in rows:
            if problem_id in invalid_problem_ids:
                continue
            if not judge_sql.strip() or not _expected_csv_is_valid(expected_output):
                invalid_problem_ids.add(problem_id)
    finally:
        csv.field_size_limit(previous_csv_limit)
    for problem_id in invalid_problem_ids:
        connection.execute(
            sa.text("UPDATE problems SET is_active = false WHERE id = :problem_id"),
            {"problem_id": problem_id},
        )


def _normalize_legacy_rows() -> None:
    _normalize_unroutable_slugs()
    op.execute("UPDATE users SET role = 'user' WHERE role NOT IN ('admin', 'user')")
    op.execute(
        "UPDATE problems SET checker = 'result' "
        "WHERE checker NOT IN ('result', 'string')"
    )
    op.execute(
        "UPDATE problems SET time_limit_ms = GREATEST(100, LEAST(time_limit_ms, 30000))"
    )
    _normalize_legacy_tags()
    op.execute(
        """
        UPDATE test_cases
        SET score = CASE
            WHEN score::text IN ('NaN', '-Infinity') OR score < 0 THEN 0
            WHEN score::text = 'Infinity' OR score > 1000000 THEN 1000000
            ELSE score
        END
        """
    )
    _deactivate_unsafe_legacy_problems()
    op.execute(
        "UPDATE submissions SET mode = 'submit' WHERE mode NOT IN ('run', 'submit')"
    )
    op.execute(
        "UPDATE submissions SET status = 'SE' "
        "WHERE status NOT IN "
        "('PENDING', 'RUNNING', 'AC', 'WA', 'CE', 'RE', 'TLE', 'SE')"
    )
    op.execute(
        """
        UPDATE submissions
        SET max_score = CASE
            WHEN max_score::text IN ('NaN', '-Infinity') OR max_score < 0 THEN 0
            WHEN max_score::text = 'Infinity' OR max_score > 100000000 THEN 100000000
            ELSE max_score
        END
        """
    )
    op.execute(
        """
        UPDATE submissions
        SET score = CASE
            WHEN score::text IN ('NaN', '-Infinity') OR score < 0 THEN 0
            WHEN score::text = 'Infinity' OR score > max_score THEN max_score
            ELSE score
        END
        """
    )
    op.execute(
        "UPDATE test_results SET status = 'SE' "
        "WHERE status NOT IN ('AC', 'WA', 'CE', 'RE', 'TLE', 'SE')"
    )
    op.execute(
        """
        UPDATE test_results
        SET max_score = CASE
            WHEN max_score::text IN ('NaN', '-Infinity') OR max_score < 0 THEN 0
            WHEN max_score::text = 'Infinity' OR max_score > 1000000 THEN 1000000
            ELSE max_score
        END,
        runtime_ms = GREATEST(runtime_ms, 0)
        """
    )
    op.execute(
        """
        UPDATE test_results
        SET score = CASE
            WHEN score::text IN ('NaN', '-Infinity') OR score < 0 THEN 0
            WHEN score::text = 'Infinity' OR score > max_score THEN max_score
            ELSE score
        END
        """
    )


def upgrade() -> None:
    _normalize_legacy_rows()
    op.create_check_constraint("ck_users_role", "users", "role IN ('admin', 'user')")
    op.create_check_constraint(
        "ck_problems_checker", "problems", "checker IN ('result', 'string')"
    )
    op.create_check_constraint(
        "ck_problems_time_limit_ms",
        "problems",
        "time_limit_ms BETWEEN 100 AND 30000",
    )
    op.create_check_constraint(
        "ck_test_cases_score",
        "test_cases",
        "score >= 0 AND score <= 1000000",
    )
    op.create_check_constraint(
        "ck_submissions_mode", "submissions", "mode IN ('run', 'submit')"
    )
    op.create_check_constraint(
        "ck_submissions_status",
        "submissions",
        "status IN ('PENDING', 'RUNNING', 'AC', 'WA', 'CE', 'RE', 'TLE', 'SE')",
    )
    op.create_check_constraint(
        "ck_submissions_scores",
        "submissions",
        "score >= 0 AND score <= max_score AND max_score <= 100000000",
    )
    op.create_check_constraint(
        "ck_test_results_status",
        "test_results",
        "status IN ('AC', 'WA', 'CE', 'RE', 'TLE', 'SE')",
    )
    op.create_check_constraint(
        "ck_test_results_scores",
        "test_results",
        "score >= 0 AND score <= max_score AND max_score <= 1000000",
    )
    op.create_check_constraint(
        "ck_test_results_runtime_ms",
        "test_results",
        "runtime_ms >= 0",
    )

    op.create_index("ix_submissions_user_id_id", "submissions", ["user_id", "id"])
    op.create_index(
        "ix_submissions_user_id_status", "submissions", ["user_id", "status"]
    )
    op.create_index("ix_submissions_problem_id", "submissions", ["problem_id"])
    op.create_index(
        "ix_test_results_submission_id_id",
        "test_results",
        ["submission_id", "id"],
    )
    op.create_index("ix_test_results_test_case_id", "test_results", ["test_case_id"])


def downgrade() -> None:
    op.drop_index("ix_test_results_test_case_id", table_name="test_results")
    op.drop_index("ix_test_results_submission_id_id", table_name="test_results")
    op.drop_index("ix_submissions_problem_id", table_name="submissions")
    op.drop_index("ix_submissions_user_id_status", table_name="submissions")
    op.drop_index("ix_submissions_user_id_id", table_name="submissions")

    op.drop_constraint("ck_test_results_runtime_ms", "test_results", type_="check")
    op.drop_constraint("ck_test_results_scores", "test_results", type_="check")
    op.drop_constraint("ck_test_results_status", "test_results", type_="check")
    op.drop_constraint("ck_submissions_scores", "submissions", type_="check")
    op.drop_constraint("ck_submissions_status", "submissions", type_="check")
    op.drop_constraint("ck_submissions_mode", "submissions", type_="check")
    op.drop_constraint("ck_test_cases_score", "test_cases", type_="check")
    op.drop_constraint("ck_problems_time_limit_ms", "problems", type_="check")
    op.drop_constraint("ck_problems_checker", "problems", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
