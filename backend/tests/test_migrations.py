from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from alembic import command
from app.config import settings
from app.services.judge import _admin_conninfo

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_legacy_database_upgrades_with_data_normalization(monkeypatch):
    try:
        with psycopg.connect(_admin_conninfo(), connect_timeout=2):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"local PostgreSQL unavailable: {exc}")

    database_name = f"oj_migration_{uuid.uuid4().hex}"
    created = False
    try:
        try:
            with psycopg.connect(_admin_conninfo(), autocommit=True) as admin:
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(database_name)
                    )
                )
            created = True
        except psycopg.errors.InsufficientPrivilege as exc:
            pytest.skip(
                f"PostgreSQL user cannot create migration test databases: {exc}"
            )

        migration_url = make_url(settings.admin_database_url).set(
            database=database_name
        )
        monkeypatch.setattr(
            settings,
            "app_database_url",
            migration_url.render_as_string(hide_password=False),
        )
        alembic = Config(str(BACKEND_ROOT / "alembic.ini"))
        alembic.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        command.upgrade(alembic, "0001_initial")

        with psycopg.connect(_admin_conninfo(database_name)) as connection:
            user_id = connection.execute(
                """
                INSERT INTO users(username, password_hash, role, created_at)
                VALUES ('legacy-user', 'hash', 'owner', now())
                RETURNING id
                """
            ).fetchone()[0]
            legacy_tags = json.dumps(
                [
                    1,
                    " ok ",
                    "x" * 70,
                    "ok",
                    {"bad": True},
                    "\t",
                    "\u00a0",
                    " a\t",
                    "a",
                    "\u2003kept\u2003",
                ],
                ensure_ascii=False,
            )
            problem_id = connection.execute(
                """
                INSERT INTO problems(
                    slug, title, statement_md, schema_md, difficulty, tags,
                    checker, ordered, time_limit_ms, is_active, created_at, updated_at
                )
                VALUES (
                    '旧 problem/..', 'Legacy', '', '', '',
                    %s::jsonb,
                    'legacy-checker', false, 0, true, now(), now()
                )
                RETURNING id
                """,
                (legacy_tags,),
            ).fetchone()[0]
            empty_problem_id = connection.execute(
                """
                INSERT INTO problems(
                    slug, title, statement_md, schema_md, difficulty, tags,
                    checker, ordered, time_limit_ms, is_active, created_at, updated_at
                )
                VALUES (
                    'legacy-empty', 'Legacy Empty', '', '', '', '[]'::jsonb,
                    'result', false, 3000, true, now(), now()
                )
                RETURNING id
                """
            ).fetchone()[0]
            whitespace_problem_id = connection.execute(
                """
                INSERT INTO problems(
                    slug, title, statement_md, schema_md, difficulty, tags,
                    checker, ordered, time_limit_ms, is_active, created_at, updated_at
                )
                VALUES (
                    'legacy-whitespace', 'Legacy Whitespace', '', '', '', '[]'::jsonb,
                    'result', false, 3000, true, now(), now()
                )
                RETURNING id
                """
            ).fetchone()[0]
            unicode_whitespace_problem_id = connection.execute(
                """
                INSERT INTO problems(
                    slug, title, statement_md, schema_md, difficulty, tags,
                    checker, ordered, time_limit_ms, is_active, created_at, updated_at
                )
                VALUES (
                    'legacy-unicode-whitespace',
                    'Legacy Unicode Whitespace', '', '', '', '[]'::jsonb,
                    'result', false, 3000, true, now(), now()
                )
                RETURNING id
                """
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO test_cases(
                    problem_id, name, is_hidden, score, setup_sql, judge_sql,
                    expected_output, created_at
                )
                VALUES (%s, 'whitespace', false, 1, '', E'\n\t', 'value\n1\n', now())
                """,
                (whitespace_problem_id,),
            )
            connection.execute(
                """
                INSERT INTO test_cases(
                    problem_id, name, is_hidden, score, setup_sql, judge_sql,
                    expected_output, created_at
                )
                VALUES (%s, 'unicode-whitespace', false, 1, '', %s, 'value\n1\n', now())
                """,
                (unicode_whitespace_problem_id, "\u0085"),
            )
            case_id = connection.execute(
                """
                INSERT INTO test_cases(
                    problem_id, name, is_hidden, score, setup_sql, judge_sql,
                    expected_output, created_at
                )
                VALUES (
                    %s, 'legacy-case', false, 'NaN', '', 'SELECT 1',
                    'value\n1\n', now()
                )
                RETURNING id
                """,
                (problem_id,),
            ).fetchone()[0]
            submission_id = connection.execute(
                """
                INSERT INTO submissions(
                    user_id, problem_id, code, mode, status,
                    score, max_score, created_at
                )
                VALUES (%s, %s, 'SELECT 1', 'legacy-mode', 'legacy-status',
                        'Infinity', '-Infinity', now())
                RETURNING id
                """,
                (user_id, problem_id),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO test_results(
                    submission_id, test_case_id, name, status, score, max_score,
                    runtime_ms, is_hidden, created_at
                )
                VALUES (%s, %s, 'legacy-case', 'legacy-status', 'NaN',
                        'Infinity', -5, false, now())
                """,
                (submission_id, case_id),
            )
            connection.commit()

        command.upgrade(alembic, "head")
        command.check(alembic)

        with psycopg.connect(_admin_conninfo(database_name)) as connection:
            assert (
                connection.execute(
                    "SELECT role FROM users WHERE id = %s", (user_id,)
                ).fetchone()[0]
                == "user"
            )
            slug, checker, timeout, tags, is_active = connection.execute(
                "SELECT slug, checker, time_limit_ms, tags, is_active "
                "FROM problems WHERE id = %s",
                (problem_id,),
            ).fetchone()
            assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
            assert checker == "result"
            assert timeout == 100
            assert tags == ["ok", "x" * 64, "a", "kept"]
            assert is_active is True
            assert (
                connection.execute(
                    "SELECT is_active FROM problems WHERE id = %s", (empty_problem_id,)
                ).fetchone()[0]
                is False
            )
            assert (
                connection.execute(
                    "SELECT is_active FROM problems WHERE id = %s",
                    (whitespace_problem_id,),
                ).fetchone()[0]
                is False
            )
            assert (
                connection.execute(
                    "SELECT is_active FROM problems WHERE id = %s",
                    (unicode_whitespace_problem_id,),
                ).fetchone()[0]
                is False
            )
            assert (
                connection.execute(
                    "SELECT score FROM test_cases WHERE id = %s", (case_id,)
                ).fetchone()[0]
                == 0
            )
            assert connection.execute(
                "SELECT mode, status, score, max_score FROM submissions WHERE id = %s",
                (submission_id,),
            ).fetchone() == ("submit", "SE", 0, 0)
            assert connection.execute(
                "SELECT status, score, max_score, runtime_ms "
                "FROM test_results WHERE submission_id = %s",
                (submission_id,),
            ).fetchone() == ("SE", 0, 1_000_000, 0)

        command.downgrade(alembic, "0001_initial")
        command.upgrade(alembic, "head")
        command.check(alembic)
    finally:
        if created:
            with psycopg.connect(_admin_conninfo(), autocommit=True) as admin:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity WHERE datname = %s",
                    (database_name,),
                )
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )
