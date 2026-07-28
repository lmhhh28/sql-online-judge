from __future__ import annotations

import csv
import io
import logging
import math
import re
import secrets
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import chain
from typing import Iterator, Optional

import psycopg
from psycopg import errors, pq, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.generators import fetch
from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import (
    DATABASE_CONNECT_TIMEOUT_SECONDS,
    psycopg_connection_url,
    settings,
)
from app.database import SessionLocal
from app.models import Problem, Submission, TestCase, TestResult, utc_now
from app.services.comparator import compare_csv_tables, compare_string
from app.services.sql_guard import (
    UnsafeSqlError,
    is_streamable_query,
    split_sql_statements,
    validate_user_sql,
)

RUN_DB_PREFIX = "oj_run_"
RUN_ROLE_PREFIX = "oj_exec_"
RUN_DB_COMMENT = "sql-oj transient judge database"
RUN_ROLE_COMMENT = "sql-oj transient judge role"
USER_OUTPUT_SENTINEL = "__USER_OUTPUT__"
MIN_TIME_LIMIT_MS = 100
MAX_TIME_LIMIT_MS = 30_000
SYSTEM_PHASE_TIME_LIMIT_MS = 30_000
MAX_OUTPUT_ROWS = 10_000
MAX_OUTPUT_COLUMNS = 256
MAX_OUTPUT_BYTES = 1_000_000
MAX_OUTPUT_CELL_BYTES = MAX_OUTPUT_BYTES
MAX_RUN_DATABASE_BYTES = 256 * 1024 * 1024
DATABASE_SIZE_POLL_SECONDS = 0.1
ADMIN_OPERATION_TIMEOUT_MS = 10_000
MAX_CONCURRENT_JUDGES = 2
MAX_QUEUED_JUDGES = 16
MAX_SUBMISSION_WALL_TIME_MS = 300_000
MAX_VISIBLE_OUTPUT_FIELD_BYTES = 256 * 1024
MAX_VISIBLE_OUTPUT_TOTAL_BYTES = 2 * 1024 * 1024
OUTPUT_TRUNCATION_MARKER = "\n... [truncated]"
MAX_PROJECTED_OUTPUT_ROWS = 8
MAX_PROJECTED_OUTPUT_CHARS = 16 * 1024
MAX_PROJECTED_MESSAGE_CHARS = 10_000
MAX_PROJECTED_RESULTS = 100
MAX_PROJECTED_CODE_CHARS = 50_000
MAX_PROJECTED_ERROR_MESSAGE_CHARS = 10_000
_NEW_RUN_DB_RE = re.compile(rf"^{re.escape(RUN_DB_PREFIX)}[0-9a-f]{{32}}$")
_LEGACY_RUN_DB_RE = re.compile(
    rf"^{re.escape(RUN_DB_PREFIX)}[1-9][0-9]*_[1-9][0-9]*_[0-9a-f]{{8}}$"
)
_RUN_ROLE_RE = re.compile(rf"^{re.escape(RUN_ROLE_PREFIX)}[0-9a-f]{{32}}$")
logger = logging.getLogger(__name__)
_judge_capacity = threading.BoundedSemaphore(MAX_QUEUED_JUDGES)
_judge_executor_lock = threading.Lock()
_judge_executor: Optional[ThreadPoolExecutor] = None
_judge_accepting = True
_judge_shutdown_requested = threading.Event()
TERMINAL_ERROR_STATUSES = {"CE", "RE", "TLE", "SE"}
STATUS_PRIORITY = {
    "AC": 0,
    "WA": 1,
    "CE": 2,
    "RE": 3,
    "TLE": 4,
    "SE": 5,
}


@dataclass(frozen=True)
class CaseOutcome:
    status: str
    score: float
    runtime_ms: int
    message: str
    actual_output: Optional[str] = None


class OutputLimitExceeded(RuntimeError):
    pass


class SqlDeadlineExceeded(TimeoutError):
    pass


class DatabaseSizeExceeded(RuntimeError):
    pass


class ResourceWatchdogFailed(RuntimeError):
    pass


class JudgeShutdownRequested(RuntimeError):
    pass


class SubmissionDeadlineExceeded(TimeoutError):
    pass


class _ServerCursorSetupFailed(RuntimeError):
    pass


class _ResultPreservingStreamCursor(psycopg.Cursor):
    """Keep the final TUPLES_OK metadata discarded by Cursor.stream().

    Psycopg 3.2.13 intentionally drops the terminal PGresult in single-row
    mode. That makes an empty DML RETURNING indistinguishable from a command
    with no result set. The dependency is exactly pinned, so mirroring this
    small private generator is preferable to buffering an unbounded result.
    """

    def _stream_fetchone_gen(self, first):
        result = yield from fetch(self._pgconn)
        if result is None:
            return None

        status = result.status
        if status in {pq.ExecStatus.SINGLE_TUPLE, pq.ExecStatus.TUPLES_CHUNK}:
            self.pgresult = result
            self._tx.set_pgresult(result, set_loaders=first)
            if first:
                self._make_row = self._make_row_maker()
            return result

        if status in {pq.ExecStatus.TUPLES_OK, pq.ExecStatus.COMMAND_OK}:
            if status == pq.ExecStatus.TUPLES_OK:
                self.pgresult = result
                self._tx.set_pgresult(result, set_loaders=first)
                if first:
                    self._make_row = self._make_row_maker()
            while result:
                result = yield from fetch(self._pgconn)
            if status != pq.ExecStatus.TUPLES_OK:
                raise errors.ProgrammingError(
                    "the operation in stream() didn't produce a result"
                )
            return None

        return self._raise_for_result(result)


class _LimitedStringIO(io.StringIO):
    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self.max_bytes = max_bytes
        self.bytes_written = 0

    def write(self, value: str) -> int:
        self.bytes_written += len(value.encode("utf-8"))
        if self.bytes_written > self.max_bytes:
            raise OutputLimitExceeded(f"Query output exceeds {self.max_bytes} bytes")
        return super().write(value)


def _psycopg_url(url: str) -> str:
    return psycopg_connection_url(url)


def _admin_conninfo(database: Optional[str] = None) -> str:
    params = conninfo_to_dict(_psycopg_url(settings.admin_database_url))
    if database is not None:
        params["dbname"] = database
    params["connect_timeout"] = str(DATABASE_CONNECT_TIMEOUT_SECONDS)
    return make_conninfo(**params)


def _runner_conninfo(database: str, role_name: str, password: str) -> str:
    params = conninfo_to_dict(_admin_conninfo(database))
    params["user"] = role_name
    params["password"] = password
    return make_conninfo(**params)


def _merge_status(current: str, candidate: str) -> str:
    if STATUS_PRIORITY.get(candidate, 0) > STATUS_PRIORITY.get(current, 0):
        return candidate
    return current


def _rows_to_limited_csv(columns, rows) -> str:
    columns = list(columns)
    if len(columns) > MAX_OUTPUT_COLUMNS:
        raise OutputLimitExceeded(f"Query output exceeds {MAX_OUTPUT_COLUMNS} columns")

    stream = _LimitedStringIO(MAX_OUTPUT_BYTES)
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(columns)
    row_count = 0
    for row in rows:
        row_count += 1
        if row_count > MAX_OUTPUT_ROWS:
            raise OutputLimitExceeded(f"Query output exceeds {MAX_OUTPUT_ROWS} rows")
        rendered_row = []
        for cell in row:
            rendered = r"\N" if cell is None else str(cell)
            if len(rendered.encode("utf-8")) > MAX_OUTPUT_CELL_BYTES:
                raise OutputLimitExceeded(
                    f"Query output cell exceeds {MAX_OUTPUT_CELL_BYTES} bytes"
                )
            rendered_row.append(rendered)
        writer.writerow(rendered_row)
    return stream.getvalue()


def _cursor_rows(cursor):
    while True:
        rows = cursor.fetchmany(1)
        if not rows:
            return
        yield rows[0]


def _cursor_to_csv(cursor) -> str:
    columns = [desc.name for desc in cursor.description or []]
    return _rows_to_limited_csv(columns, _cursor_rows(cursor))


def _valid_run_database_name(db_name: str) -> bool:
    return bool(
        _NEW_RUN_DB_RE.fullmatch(db_name) or _LEGACY_RUN_DB_RE.fullmatch(db_name)
    )


def _valid_run_role_name(role_name: str) -> bool:
    return bool(_RUN_ROLE_RE.fullmatch(role_name))


def _configured_database_names() -> set[str]:
    names: set[str] = set()
    for url in (settings.app_database_url, settings.admin_database_url):
        database_name = conninfo_to_dict(_psycopg_url(url)).get("dbname")
        if database_name:
            names.add(database_name)
    return names


def _configured_role_names() -> set[str]:
    names = {settings.judge_runner_role}
    for url in (settings.app_database_url, settings.admin_database_url):
        role_name = conninfo_to_dict(_psycopg_url(url)).get("user")
        if role_name:
            names.add(role_name)
    return names


def _expected_run_role_for_database(db_name: str) -> Optional[str]:
    if not _NEW_RUN_DB_RE.fullmatch(db_name):
        return None
    return f"{RUN_ROLE_PREFIX}{db_name[len(RUN_DB_PREFIX) :]}"


def _expected_run_database_for_role(role_name: str) -> Optional[str]:
    if not _RUN_ROLE_RE.fullmatch(role_name):
        return None
    return f"{RUN_DB_PREFIX}{role_name[len(RUN_ROLE_PREFIX) :]}"


def _is_restricted_run_role(role_record) -> bool:
    (
        _role_name,
        _can_login,
        is_superuser,
        can_create_db,
        can_create_role,
        inherits,
        can_replicate,
        can_bypass_rls,
        connection_limit,
        has_inherited_roles,
        comment,
    ) = role_record
    return (
        not is_superuser
        and not can_create_db
        and not can_create_role
        and not inherits
        and not can_replicate
        and not can_bypass_rls
        and connection_limit == 1
        and not has_inherited_roles
        and comment == RUN_ROLE_COMMENT
    )


def _terminate_run_backend(pid: int, db_name: str, role_name: str) -> None:
    if not _NEW_RUN_DB_RE.fullmatch(db_name) or not _valid_run_role_name(role_name):
        raise ValueError("Refusing to terminate a backend outside a current judge run")
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        conn.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE pid = %s AND datname = %s AND usename = %s
            """,
            (pid, db_name, role_name),
        )


@contextmanager
def _query_deadline(
    conn,
    timeout_ms: int,
    db_name: str,
    role_name: str,
    *,
    monitor_database_size: bool = True,
) -> Iterator[None]:
    expired = threading.Event()
    database_too_large = threading.Event()
    resource_watchdog_failed = threading.Event()
    shutdown_requested = threading.Event()
    finished = threading.Event()
    cancellation_started = threading.Event()
    cancellation_lock = threading.Lock()
    backend_pid = conn.info.backend_pid

    def interrupt_query() -> None:
        with cancellation_lock:
            if cancellation_started.is_set():
                return
            cancellation_started.set()
        try:
            conn.cancel_safe(timeout=1.0)
        except Exception:
            logger.warning(
                "Safe query cancellation failed for database %s; terminating backend",
                db_name,
                exc_info=True,
            )
        if not finished.wait(0.25):
            try:
                _terminate_run_backend(backend_pid, db_name, role_name)
            except Exception:
                logger.warning(
                    "Failed to terminate timed-out judge backend for %s",
                    db_name,
                    exc_info=True,
                )

    def expire_query() -> None:
        if finished.is_set():
            return
        expired.set()
        if finished.is_set():
            expired.clear()
            return
        interrupt_query()

    def monitor_size() -> None:
        if _judge_shutdown_requested.is_set():
            shutdown_requested.set()
            interrupt_query()
            return
        if not monitor_database_size:
            while not finished.wait(0.05):
                if _judge_shutdown_requested.is_set():
                    shutdown_requested.set()
                    interrupt_query()
                    return
            return
        try:
            with psycopg.connect(_admin_conninfo(), autocommit=True) as admin_conn:
                admin_conn.execute(
                    f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}"
                )
                while True:
                    if _judge_shutdown_requested.is_set():
                        shutdown_requested.set()
                        if not finished.is_set():
                            interrupt_query()
                        return
                    size = admin_conn.execute(
                        "SELECT pg_database_size(%s)", (db_name,)
                    ).fetchone()[0]
                    if size is not None and size > MAX_RUN_DATABASE_BYTES:
                        database_too_large.set()
                        if not finished.is_set():
                            interrupt_query()
                        return
                    if finished.is_set():
                        return
                    if finished.wait(DATABASE_SIZE_POLL_SECONDS):
                        # Loop once more for a final size check after the query
                        # completed; the database may have grown between polls.
                        continue
        except Exception:
            logger.warning(
                "Database-size watchdog failed for %s", db_name, exc_info=True
            )
            resource_watchdog_failed.set()
            if not finished.is_set():
                interrupt_query()

    timer = threading.Timer(timeout_ms / 1000, expire_query)
    timer.daemon = True
    timer.start()
    size_monitor = threading.Thread(
        target=monitor_size, name=f"judge-resource-{db_name}", daemon=True
    )
    size_monitor.start()
    caught: BaseException | None = None
    try:
        yield
    except BaseException as exc:
        caught = exc
        raise
    finally:
        finished.set()
        timer.cancel()
        timer.join(timeout=6.0)
        watchdog_stuck = timer.is_alive()
        if timer.is_alive():
            logger.warning(
                "Judge timeout watchdog did not stop promptly for database %s", db_name
            )
        size_monitor.join(timeout=6.0)
        watchdog_stuck = watchdog_stuck or size_monitor.is_alive()
        if size_monitor.is_alive():
            logger.warning(
                "Judge resource watchdog did not stop promptly for database %s", db_name
            )
        if watchdog_stuck:
            resource_watchdog_failed.set()
            try:
                conn.close()
            except Exception:
                logger.warning(
                    "Failed to close connection after a stuck judge watchdog",
                    exc_info=True,
                )
        if shutdown_requested.is_set() and not isinstance(
            caught, JudgeShutdownRequested
        ):
            raise JudgeShutdownRequested("Judge service is shutting down") from caught
        if resource_watchdog_failed.is_set() and not isinstance(
            caught, ResourceWatchdogFailed
        ):
            raise ResourceWatchdogFailed("Database-size watchdog failed") from caught
        if database_too_large.is_set() and not isinstance(caught, DatabaseSizeExceeded):
            raise DatabaseSizeExceeded(
                "Temporary database exceeded the size limit"
            ) from caught
        if expired.is_set() and not isinstance(caught, SqlDeadlineExceeded):
            raise SqlDeadlineExceeded(
                "SQL execution exceeded the wall-clock limit"
            ) from caught


def _effective_timeout_ms(value: object) -> int:
    try:
        timeout_ms = int(value)
    except (TypeError, ValueError):
        timeout_ms = MIN_TIME_LIMIT_MS
    return max(MIN_TIME_LIMIT_MS, min(timeout_ms, MAX_TIME_LIMIT_MS))


def _system_phase_timeout_ms(timeout_ms: int) -> int:
    return min(SYSTEM_PHASE_TIME_LIMIT_MS, max(10_000, timeout_ms * 2))


def _phase_timeout_ms(
    desired_ms: int, absolute_deadline: Optional[float]
) -> tuple[int, bool]:
    if absolute_deadline is None:
        return desired_ms, False
    remaining_ms = int((absolute_deadline - time.monotonic()) * 1000)
    if remaining_ms <= 0:
        raise SubmissionDeadlineExceeded
    return max(1, min(desired_ms, remaining_ms)), remaining_ms < desired_ms


def _remaining_submission_budget_ms(started: float) -> int:
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return max(0, MAX_SUBMISSION_WALL_TIME_MS - elapsed_ms)


def _submission_deadline_reached(absolute_deadline: Optional[float]) -> bool:
    return absolute_deadline is not None and time.monotonic() >= absolute_deadline


def _stored_output_budgets(cases: list[TestCase]) -> dict[bool, int]:
    visibilities = {case.is_hidden for case in cases}
    if visibilities == {False, True}:
        public_budget = MAX_VISIBLE_OUTPUT_TOTAL_BYTES // 2
        return {
            False: public_budget,
            True: MAX_VISIBLE_OUTPUT_TOTAL_BYTES - public_budget,
        }
    return {
        False: MAX_VISIBLE_OUTPUT_TOTAL_BYTES if False in visibilities else 0,
        True: MAX_VISIBLE_OUTPUT_TOTAL_BYTES if True in visibilities else 0,
    }


def _user_error_message(exc: psycopg.Error) -> str:
    primary = getattr(exc.diag, "message_primary", None) or exc.__class__.__name__
    primary = " ".join(primary.split())
    if len(primary) > 500:
        primary = primary[:500] + "..."
    return primary


def cleanup_stale_run_databases() -> int:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        current_database, current_user, session_user = conn.execute(
            "SELECT current_database(), current_user, session_user"
        ).fetchone()
        protected_databases = _configured_database_names() | {current_database}
        protected_roles = _configured_role_names() | {current_user, session_user}
        role_records = {
            row[0]: row
            for row in conn.execute(
                """
                SELECT
                    role.rolname,
                    role.rolcanlogin,
                    role.rolsuper,
                    role.rolcreatedb,
                    role.rolcreaterole,
                    role.rolinherit,
                    role.rolreplication,
                    role.rolbypassrls,
                    role.rolconnlimit,
                    EXISTS (
                        SELECT 1 FROM pg_auth_members membership
                        WHERE membership.member = role.oid
                    ),
                    shobj_description(role.oid, 'pg_authid')
                FROM pg_roles role
                WHERE left(role.rolname, %s) = %s
                """,
                (len(RUN_ROLE_PREFIX), RUN_ROLE_PREFIX),
            )
            if _valid_run_role_name(row[0])
        }
        run_databases = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                """
                SELECT
                    database.datname,
                    owner.rolname,
                    shobj_description(database.oid, 'pg_database')
                FROM pg_database database
                JOIN pg_roles owner ON owner.oid = database.datdba
                WHERE left(database.datname, %s) = %s
                """,
                (len(RUN_DB_PREFIX), RUN_DB_PREFIX),
            )
            if _valid_run_database_name(row[0])
        }

    db_names = []
    for db_name, (owner_role, comment) in run_databases.items():
        if db_name in protected_databases:
            continue
        expected_role = _expected_run_role_for_database(db_name)
        if expected_role is None:
            if owner_role == settings.judge_runner_role:
                db_names.append(db_name)
            continue
        owner_record = role_records.get(expected_role)
        if (
            owner_role == expected_role
            and owner_role not in protected_roles
            and owner_record is not None
            and _is_restricted_run_role(owner_record)
            and comment == RUN_DB_COMMENT
        ):
            db_names.append(db_name)

    role_names = []
    for role_name, role_record in role_records.items():
        if role_name in protected_roles or not _is_restricted_run_role(role_record):
            continue
        expected_database = _expected_run_database_for_role(role_name)
        if expected_database in run_databases and expected_database not in db_names:
            # A same-token database exists but isn't demonstrably ours.
            continue
        role_names.append(role_name)

    dropped = 0
    failed_databases: set[str] = set()
    for db_name in db_names:
        try:
            _drop_run_database(db_name)
            dropped += 1
        except Exception:
            failed_databases.add(db_name)
            logger.warning(
                "Failed to drop stale judge database %s", db_name, exc_info=True
            )
    for role_name in role_names:
        if _expected_run_database_for_role(role_name) in failed_databases:
            continue
        try:
            _drop_run_role(role_name)
        except Exception:
            logger.warning(
                "Failed to drop stale judge role %s", role_name, exc_info=True
            )
    return dropped


def recover_interrupted_judges() -> tuple[int, int]:
    db = SessionLocal()
    try:
        interrupted = list(
            db.scalars(
                select(Submission).where(Submission.status.in_(("PENDING", "RUNNING")))
            )
        )
        now = utc_now()
        for submission in interrupted:
            submission.status = "SE"
            submission.error_message = "Judge interrupted before completion"
            submission.completed_at = now
        db.commit()
    finally:
        db.close()

    # Commit application-visible recovery before cluster cleanup. If the
    # administrator connection is temporarily unavailable, interrupted
    # submissions must not remain stuck in a state the UI will poll forever.
    dropped = cleanup_stale_run_databases()
    return len(interrupted), dropped


def _create_run_role(role_name: str, password: str) -> None:
    if not _valid_run_role_name(role_name):
        raise ValueError("Invalid judge role name")
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        created = False
        try:
            password_expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat()
            conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} VALID UNTIL {} CONNECTION LIMIT 1 "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                ).format(
                    sql.Identifier(role_name),
                    sql.Literal(password),
                    sql.Literal(password_expires_at),
                )
            )
            created = True
            conn.execute(
                sql.SQL("COMMENT ON ROLE {} IS {}").format(
                    sql.Identifier(role_name),
                    sql.Literal(RUN_ROLE_COMMENT),
                )
            )
            admin_role = conn.execute("SELECT current_user").fetchone()[0]
            conn.execute(
                sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                    sql.Identifier(role_name),
                    sql.Identifier(admin_role),
                )
            )
            for setting, value in (
                ("work_mem", "16MB"),
                ("maintenance_work_mem", "32MB"),
                ("temp_file_limit", "64MB"),
                ("max_parallel_workers_per_gather", "0"),
                ("max_parallel_maintenance_workers", "0"),
                ("jit", "off"),
            ):
                conn.execute(
                    sql.SQL("ALTER ROLE {} SET {} TO {}").format(
                        sql.Identifier(role_name),
                        sql.Identifier(setting),
                        sql.Literal(value),
                    )
                )
        except Exception:
            if created:
                try:
                    conn.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(role_name)
                        )
                    )
                except Exception:
                    logger.warning(
                        "Failed to compensate after creating judge role %s",
                        role_name,
                        exc_info=True,
                    )
            raise


def _disable_run_role_login(role_name: str) -> None:
    if not _valid_run_role_name(role_name):
        raise ValueError("Invalid judge role name")
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        conn.execute(
            sql.SQL("ALTER ROLE {} NOLOGIN PASSWORD NULL").format(
                sql.Identifier(role_name)
            )
        )


def _create_run_database(db_name: str, owner_role: str) -> None:
    if not _NEW_RUN_DB_RE.fullmatch(db_name):
        raise ValueError("Invalid judge database name")
    if owner_role != _expected_run_role_for_database(db_name):
        raise ValueError("Judge database and owner role tokens must match")
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        created = False
        try:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
                    sql.Identifier(db_name),
                    sql.Identifier(owner_role),
                )
            )
            created = True
            conn.execute(
                sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                    sql.Identifier(db_name),
                    sql.Literal(RUN_DB_COMMENT),
                )
            )
            conn.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(db_name)
                )
            )
            admin_role = conn.execute("SELECT current_user").fetchone()[0]
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                    sql.Identifier(db_name),
                    sql.Identifier(owner_role),
                    sql.Identifier(admin_role),
                )
            )
        except Exception:
            if created:
                try:
                    conn.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(db_name)
                        )
                    )
                except Exception:
                    logger.warning(
                        "Failed to compensate after creating judge database %s",
                        db_name,
                        exc_info=True,
                    )
            raise


def _drop_run_database(db_name: str) -> None:
    if not _valid_run_database_name(db_name):
        raise ValueError("Refusing to drop a database outside the judge namespace")
    if db_name in _configured_database_names():
        raise ValueError(
            "Refusing to drop a configured application or administrator database"
        )
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        if conn.execute("SELECT current_database()").fetchone()[0] == db_name:
            raise ValueError("Refusing to drop the current administrator database")
        prepared_transactions = conn.execute(
            "SELECT gid, owner FROM pg_prepared_xacts WHERE database = %s",
            (db_name,),
        ).fetchall()
    if prepared_transactions:
        with psycopg.connect(_admin_conninfo(db_name), autocommit=True) as run_conn:
            run_conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
            for gid, owner in prepared_transactions:
                run_conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(owner)))
                try:
                    run_conn.execute(
                        sql.SQL("ROLLBACK PREPARED {}").format(sql.Literal(gid))
                    )
                finally:
                    run_conn.execute("RESET ROLE")
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (db_name,),
        )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(db_name)
            )
        )


def _drop_run_role(role_name: str) -> None:
    if not _valid_run_role_name(role_name):
        raise ValueError("Refusing to drop a role outside the judge namespace")
    if role_name in _configured_role_names():
        raise ValueError("Refusing to drop a configured database role")
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(f"SET statement_timeout TO {ADMIN_OPERATION_TIMEOUT_MS}")
        current_user, session_user = conn.execute(
            "SELECT current_user, session_user"
        ).fetchone()
        if role_name in {current_user, session_user}:
            raise ValueError("Refusing to drop the current database role")
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = %s",
            (role_name,),
        )
        conn.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
        )


def _set_session_limits(conn, timeout_ms: int) -> None:
    conn.execute("SET standard_conforming_strings TO on")
    conn.execute(f"SET statement_timeout TO {timeout_ms}")
    conn.execute(f"SET lock_timeout TO {timeout_ms}")
    conn.execute(f"SET idle_in_transaction_session_timeout TO {timeout_ms}")
    conn.execute("SET work_mem TO '16MB'")
    conn.execute("SET maintenance_work_mem TO '32MB'")
    conn.execute("SET max_parallel_workers_per_gather TO 0")
    conn.execute("SET max_parallel_maintenance_workers TO 0")
    conn.execute("SET jit TO off")


def _consume_result_sets(cursor, collect_first: bool) -> tuple[int, Optional[str]]:
    result_count = 0
    first_result: Optional[str] = None
    while True:
        if cursor.description:
            rendered = _cursor_to_csv(cursor)
            result_count += 1
            if collect_first and first_result is None:
                first_result = rendered
        if not cursor.nextset():
            break
    return result_count, first_result


def _stream_statement(conn, statement: str) -> Optional[str]:
    """Execute one statement in libpq single-row mode when it returns rows."""
    cursor = _ResultPreservingStreamCursor(conn)
    row_stream = cursor.stream(statement, size=1)
    try:
        try:
            first_row = next(row_stream)
        except StopIteration:
            if cursor.description:
                columns = [desc.name for desc in cursor.description]
                return _rows_to_limited_csv(columns, ())
            return None
        except psycopg.ProgrammingError as exc:
            if "didn't produce a result" in str(exc):
                return None
            raise
        columns = [desc.name for desc in cursor.description or []]
        return _rows_to_limited_csv(columns, chain((first_row,), row_stream))
    finally:
        try:
            row_stream.close()
        finally:
            cursor.close()


def _execute_user_sql(
    conn, user_sql: str, collect_first: bool
) -> tuple[int, Optional[str]]:
    """Execute user statements, streaming ordinary queries through server cursors."""
    result_count = 0
    first_result: Optional[str] = None
    for statement in split_sql_statements(user_sql):
        rendered: Optional[str] = None
        if is_streamable_query(statement):
            try:
                with conn.transaction():
                    with conn.cursor(name=f"oj_cursor_{uuid.uuid4().hex}") as cursor:
                        try:
                            cursor.execute(statement)
                        except psycopg.Error as exc:
                            # DECLARE CURSOR rejects valid SELECT INTO and
                            # data-modifying CTEs before executing them. Retry
                            # those exceptional shapes as an ordinary command.
                            raise _ServerCursorSetupFailed from exc
                        if cursor.description:
                            rendered = _cursor_to_csv(cursor)
            except _ServerCursorSetupFailed:
                rendered = _stream_statement(conn, statement)
        else:
            rendered = _stream_statement(conn, statement)

        if rendered is not None:
            result_count += 1
            if collect_first and first_result is None:
                first_result = rendered
    return result_count, first_result


def _run_sql_case(
    submission: Submission,
    problem: Problem,
    case: TestCase,
    total_time_limit_ms: Optional[int] = None,
) -> CaseOutcome:
    run_token = uuid.uuid4().hex
    run_db = f"{RUN_DB_PREFIX}{run_token}"
    run_role = f"{RUN_ROLE_PREFIX}{run_token}"
    run_password = secrets.token_urlsafe(32)
    started = time.perf_counter()
    absolute_deadline = (
        None
        if total_time_limit_ms is None
        else time.monotonic() + max(0, total_time_limit_ms) / 1000
    )
    user_runtime_ms = 0
    role_created = False
    database_created = False
    try:
        if _judge_shutdown_requested.is_set():
            raise JudgeShutdownRequested
        if _submission_deadline_reached(absolute_deadline):
            raise SubmissionDeadlineExceeded
        validate_user_sql(submission.code)
        if _submission_deadline_reached(absolute_deadline):
            raise SubmissionDeadlineExceeded
        timeout_ms = _effective_timeout_ms(problem.time_limit_ms)
        system_timeout_ms = _system_phase_timeout_ms(timeout_ms)
        _create_run_role(run_role, run_password)
        role_created = True
        if _submission_deadline_reached(absolute_deadline):
            raise SubmissionDeadlineExceeded
        _create_run_database(run_db, owner_role=run_role)
        database_created = True

        if _judge_shutdown_requested.is_set():
            raise JudgeShutdownRequested
        if _submission_deadline_reached(absolute_deadline):
            raise SubmissionDeadlineExceeded

        with psycopg.connect(
            _runner_conninfo(run_db, run_role, run_password), autocommit=True
        ) as conn:
            # Authentication is complete, so remove the transient role's ability
            # to establish any additional connection before executing user SQL.
            _disable_run_role_login(run_role)
            if _submission_deadline_reached(absolute_deadline):
                raise SubmissionDeadlineExceeded

            setup_uses_submission_deadline = False
            try:
                if case.setup_sql.strip():
                    setup_timeout_ms, setup_uses_submission_deadline = (
                        _phase_timeout_ms(
                            system_timeout_ms,
                            absolute_deadline,
                        )
                    )
                    _set_session_limits(conn, setup_timeout_ms)
                    with _query_deadline(conn, setup_timeout_ms, run_db, run_role):
                        _execute_user_sql(conn, case.setup_sql, collect_first=False)
            except SubmissionDeadlineExceeded:
                return CaseOutcome("TLE", 0.0, 0, "Submission time limit exceeded")
            except (SqlDeadlineExceeded, errors.QueryCanceled):
                if setup_uses_submission_deadline or _submission_deadline_reached(
                    absolute_deadline
                ):
                    return CaseOutcome("TLE", 0.0, 0, "Submission time limit exceeded")
                logger.warning(
                    "Test setup timed out for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome("SE", 0.0, 0, "Test setup timed out")
            except JudgeShutdownRequested:
                return CaseOutcome("SE", 0.0, 0, "Judge stopped during shutdown")
            except ResourceWatchdogFailed:
                logger.warning(
                    "Test setup resource watchdog failed for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE", 0.0, 0, "Test setup resource monitoring failed"
                )
            except DatabaseSizeExceeded:
                logger.warning(
                    "Test setup exceeded database size limit for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE", 0.0, 0, "Test setup exceeded the database size limit"
                )
            except OutputLimitExceeded:
                logger.warning(
                    "Test setup output exceeded limits for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE", 0.0, 0, "Test setup output exceeded the configured limit"
                )
            except Exception:
                logger.exception(
                    "Test setup failed for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome("SE", 0.0, 0, "Test setup failed")

            user_started = time.perf_counter()
            user_uses_submission_deadline = False
            try:
                user_output = None
                capture_user_output = case.judge_sql.strip() == USER_OUTPUT_SENTINEL
                if submission.code.strip():
                    user_timeout_ms, user_uses_submission_deadline = _phase_timeout_ms(
                        timeout_ms,
                        absolute_deadline,
                    )
                    _set_session_limits(conn, user_timeout_ms)
                    with _query_deadline(conn, user_timeout_ms, run_db, run_role):
                        result_count, user_output = _execute_user_sql(
                            conn,
                            submission.code,
                            collect_first=capture_user_output,
                        )
                    user_runtime_ms = _elapsed_ms(user_started)
                    if capture_user_output and result_count > 1:
                        return CaseOutcome(
                            "WA",
                            0.0,
                            user_runtime_ms,
                            "User SQL returned multiple result sets; return exactly one result set",
                        )
                elif _submission_deadline_reached(absolute_deadline):
                    raise SubmissionDeadlineExceeded
            except SubmissionDeadlineExceeded:
                return CaseOutcome(
                    "TLE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    "Submission time limit exceeded",
                )
            except JudgeShutdownRequested:
                return CaseOutcome(
                    "SE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    "Judge stopped during shutdown",
                )
            except ResourceWatchdogFailed:
                logger.warning(
                    "User SQL resource watchdog failed for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    "Judge resource monitoring failed",
                )
            except DatabaseSizeExceeded:
                return CaseOutcome(
                    "RE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    f"Database size limit exceeded ({MAX_RUN_DATABASE_BYTES // (1024 * 1024)} MiB)",
                )
            except (SqlDeadlineExceeded, errors.QueryCanceled):
                message = (
                    "Submission time limit exceeded"
                    if user_uses_submission_deadline
                    or _submission_deadline_reached(absolute_deadline)
                    else "Time limit exceeded"
                )
                return CaseOutcome(
                    "TLE", 0.0, user_runtime_ms or _elapsed_ms(user_started), message
                )
            except OutputLimitExceeded:
                return CaseOutcome(
                    "RE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    f"Output limit exceeded ({MAX_OUTPUT_ROWS} rows or {MAX_OUTPUT_BYTES} bytes)",
                )
            except errors.SyntaxError as exc:
                return CaseOutcome(
                    "CE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    f"Syntax error: {_user_error_message(exc)}",
                )
            except psycopg.Error as exc:
                return CaseOutcome(
                    "RE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    f"Database error: {_user_error_message(exc)}",
                )
            except Exception:
                logger.exception(
                    "Unexpected user SQL failure for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE",
                    0.0,
                    user_runtime_ms or _elapsed_ms(user_started),
                    "Judge execution failed",
                )

            checker_uses_submission_deadline = False
            try:
                if case.judge_sql.strip() == USER_OUTPUT_SENTINEL:
                    if user_output is None:
                        return CaseOutcome(
                            "WA",
                            0.0,
                            user_runtime_ms,
                            "User SQL did not return a result set",
                        )
                    if _submission_deadline_reached(absolute_deadline):
                        raise SubmissionDeadlineExceeded
                    actual = user_output
                else:
                    checker_timeout_ms, checker_uses_submission_deadline = (
                        _phase_timeout_ms(
                            system_timeout_ms,
                            absolute_deadline,
                        )
                    )
                    _set_session_limits(conn, checker_timeout_ms)
                    with _query_deadline(conn, checker_timeout_ms, run_db, run_role):
                        result_count, actual = _execute_user_sql(
                            conn,
                            case.judge_sql,
                            collect_first=True,
                        )
                    if result_count != 1 or actual is None:
                        return CaseOutcome(
                            "SE",
                            0.0,
                            user_runtime_ms,
                            "Checker must return exactly one result set",
                        )
            except SubmissionDeadlineExceeded:
                return CaseOutcome(
                    "TLE", 0.0, user_runtime_ms, "Submission time limit exceeded"
                )
            except (SqlDeadlineExceeded, errors.QueryCanceled):
                if checker_uses_submission_deadline or _submission_deadline_reached(
                    absolute_deadline
                ):
                    return CaseOutcome(
                        "TLE", 0.0, user_runtime_ms, "Submission time limit exceeded"
                    )
                logger.warning(
                    "Checker query timed out for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE", 0.0, user_runtime_ms, "Checker query timed out"
                )
            except JudgeShutdownRequested:
                return CaseOutcome(
                    "SE", 0.0, user_runtime_ms, "Judge stopped during shutdown"
                )
            except ResourceWatchdogFailed:
                logger.warning(
                    "Checker resource watchdog failed for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE", 0.0, user_runtime_ms, "Checker resource monitoring failed"
                )
            except DatabaseSizeExceeded:
                logger.warning(
                    "Checker exceeded database size limit for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE",
                    0.0,
                    user_runtime_ms,
                    "Checker exceeded the database size limit",
                )
            except OutputLimitExceeded:
                logger.warning(
                    "Checker output exceeded limits for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome(
                    "SE",
                    0.0,
                    user_runtime_ms,
                    "Checker output exceeded the configured limit",
                )
            except Exception:
                logger.exception(
                    "Checker query failed for submission %s case %s",
                    submission.id,
                    case.id,
                )
                return CaseOutcome("SE", 0.0, user_runtime_ms, "Checker query failed")

        if _submission_deadline_reached(absolute_deadline):
            raise SubmissionDeadlineExceeded
        if problem.checker == "string":
            compare = compare_string(actual, case.expected_output)
        else:
            compare = compare_csv_tables(
                actual, case.expected_output, ordered=problem.ordered
            )
        if _submission_deadline_reached(absolute_deadline):
            raise SubmissionDeadlineExceeded

        accepted = compare.accepted
        return CaseOutcome(
            "AC" if accepted else "WA",
            case.score if accepted else 0.0,
            user_runtime_ms,
            compare.message,
            actual,
        )
    except UnsafeSqlError as exc:
        return CaseOutcome("RE", 0.0, 0, f"Blocked SQL: {exc}")
    except SubmissionDeadlineExceeded:
        return CaseOutcome(
            "TLE", 0.0, user_runtime_ms, "Submission time limit exceeded"
        )
    except JudgeShutdownRequested:
        return CaseOutcome("SE", 0.0, user_runtime_ms, "Judge stopped during shutdown")
    except Exception:
        logger.exception(
            "Judge infrastructure failed for submission %s case %s",
            submission.id,
            case.id,
        )
        return CaseOutcome(
            "SE", 0.0, _elapsed_ms(started), "Judge infrastructure failed"
        )
    finally:
        if database_created:
            try:
                _drop_run_database(run_db)
            except Exception:
                logger.warning(
                    "Failed to clean judge database %s", run_db, exc_info=True
                )
        if role_created:
            try:
                _drop_run_role(run_role)
            except Exception:
                logger.warning("Failed to clean judge role %s", run_role, exc_info=True)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _judge_submission_impl(submission_id: int) -> None:
    db = SessionLocal()
    try:
        # Claim once under a row lock. Duplicate background dispatches otherwise
        # race and create duplicate results for the same submission.
        submission = db.scalar(
            select(Submission)
            .where(Submission.id == submission_id, Submission.status == "PENDING")
            .with_for_update(skip_locked=True)
        )
        if submission is None:
            return

        problem = submission.problem
        submission.status = "RUNNING"
        db.commit()

        case_query = (
            select(TestCase)
            .where(TestCase.problem_id == problem.id)
            .order_by(TestCase.id)
        )
        if submission.mode == "run":
            case_query = case_query.where(TestCase.is_hidden.is_(False))
        cases = list(db.scalars(case_query))

        if not cases:
            submission.status = "SE"
            submission.error_message = "No test cases selected"
            submission.completed_at = utc_now()
            db.commit()
            return

        max_score = math.fsum(case.score for case in cases)
        awarded_scores: list[float] = []
        final_status = "AC"
        submission_started = time.monotonic()
        stored_output_bytes_remaining = _stored_output_budgets(cases)

        for case in cases:
            if _judge_shutdown_requested.is_set():
                final_status = _merge_status(final_status, "SE")
                break
            remaining_budget_ms = _remaining_submission_budget_ms(submission_started)
            if remaining_budget_ms <= 0:
                outcome = CaseOutcome("TLE", 0.0, 0, "Submission time limit exceeded")
                submission_budget_exhausted = True
            else:
                outcome = _run_sql_case(
                    submission,
                    problem,
                    case,
                    total_time_limit_ms=remaining_budget_ms,
                )
                submission_budget_exhausted = (
                    _remaining_submission_budget_ms(submission_started) <= 0
                )
                if (
                    submission_budget_exhausted
                    and STATUS_PRIORITY.get(outcome.status, 0) < STATUS_PRIORITY["TLE"]
                ):
                    outcome = CaseOutcome(
                        "TLE",
                        0.0,
                        outcome.runtime_ms,
                        "Submission time limit exceeded",
                    )
            awarded_scores.append(outcome.score)
            final_status = _merge_status(final_status, outcome.status)

            stored_actual_output, used, actual_output_limited = _bounded_visible_output(
                outcome.actual_output,
                stored_output_bytes_remaining[case.is_hidden],
            )
            stored_output_bytes_remaining[case.is_hidden] -= used
            stored_expected_output, used, expected_output_limited = (
                _bounded_visible_output(
                    case.expected_output,
                    stored_output_bytes_remaining[case.is_hidden],
                )
            )
            stored_output_bytes_remaining[case.is_hidden] -= used
            stored_message = outcome.message
            if actual_output_limited or expected_output_limited:
                stored_message = (
                    f"{stored_message or ''} (stored output truncated)".strip()
                )

            db.add(
                TestResult(
                    submission_id=submission.id,
                    test_case_id=case.id,
                    name=case.name,
                    status=outcome.status,
                    score=outcome.score,
                    max_score=case.score,
                    runtime_ms=outcome.runtime_ms,
                    message=stored_message,
                    actual_output=stored_actual_output,
                    expected_output=stored_expected_output,
                    is_hidden=case.is_hidden,
                )
            )
            db.commit()

            if _judge_shutdown_requested.is_set():
                final_status = _merge_status(final_status, "SE")
                break
            if submission_budget_exhausted:
                break
            if (
                submission.mode == "submit"
                and outcome.status in TERMINAL_ERROR_STATUSES
            ):
                break

        submission.score = math.fsum(awarded_scores)
        submission.max_score = max_score
        submission.status = final_status
        submission.error_message = (
            None if final_status == "AC" else "Some test cases failed"
        )
        submission.completed_at = utc_now()
        db.commit()
    except Exception:
        logger.exception("Judge submission %s failed", submission_id)
        db.rollback()
        try:
            submission = db.get(Submission, submission_id)
            if submission is not None:
                submission.status = "SE"
                submission.error_message = "Judge failed due to an internal error"
                submission.completed_at = utc_now()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def _mark_submission_unavailable(submission_id: int, message: str) -> None:
    db = SessionLocal()
    try:
        submission = db.scalar(
            select(Submission)
            .where(Submission.id == submission_id, Submission.status == "PENDING")
            .with_for_update(skip_locked=True)
        )
        if submission is None:
            return
        submission.status = "SE"
        submission.error_message = message
        submission.completed_at = utc_now()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to mark unavailable submission %s", submission_id)
    finally:
        db.close()


def _mark_submission_overloaded(submission_id: int) -> None:
    _mark_submission_unavailable(
        submission_id, "Judge queue is full; please submit again later"
    )


def _judge_finished(future: Future, submission_id: int) -> None:
    _judge_capacity.release()
    if future.cancelled():
        _mark_submission_unavailable(
            submission_id, "Judge stopped before this submission could run"
        )
        return
    try:
        failure = future.exception()
    except BaseException:
        logger.exception("Failed to inspect completed judge task")
        return
    if failure is not None:
        logger.error(
            "Judge worker escaped its top-level error handler",
            exc_info=(type(failure), failure, failure.__traceback__),
        )


def start_judge_executor() -> None:
    """Allow lazy worker creation after application startup (idempotent)."""
    global _judge_accepting
    with _judge_executor_lock:
        _judge_shutdown_requested.clear()
        _judge_accepting = True


def shutdown_judge_executor(wait: bool = True) -> None:
    """Stop accepting work, cancel queued jobs, and interrupt running SQL."""
    global _judge_accepting, _judge_executor
    with _judge_executor_lock:
        _judge_accepting = False
        _judge_shutdown_requested.set()
        executor = _judge_executor
        _judge_executor = None
    if executor is not None:
        executor.shutdown(wait=wait, cancel_futures=True)


def judge_submission(submission_id: int) -> None:
    """Queue judging on a small dedicated pool without blocking Starlette's pool."""
    global _judge_executor
    if not _judge_capacity.acquire(blocking=False):
        logger.warning("Judge queue is full; rejecting submission %s", submission_id)
        _mark_submission_overloaded(submission_id)
        return
    try:
        with _judge_executor_lock:
            if not _judge_accepting:
                future = None
            else:
                if _judge_executor is None:
                    _judge_executor = ThreadPoolExecutor(
                        max_workers=MAX_CONCURRENT_JUDGES,
                        thread_name_prefix="sql-judge",
                    )
                future = _judge_executor.submit(_judge_submission_impl, submission_id)
    except Exception:
        _judge_capacity.release()
        logger.exception("Failed to enqueue submission %s", submission_id)
        _mark_submission_overloaded(submission_id)
        return
    if future is None:
        _judge_capacity.release()
        _mark_submission_unavailable(submission_id, "Judge service is shutting down")
        return
    future.add_done_callback(
        lambda completed: _judge_finished(completed, submission_id)
    )


def _bounded_visible_output(
    value: Optional[str], remaining_bytes: int
) -> tuple[Optional[str], int, bool]:
    if value is None:
        return None, 0, False
    encoded = value.encode("utf-8")
    allowance = max(0, min(MAX_VISIBLE_OUTPUT_FIELD_BYTES, remaining_bytes))
    if len(encoded) <= allowance:
        return value, len(encoded), False

    marker = OUTPUT_TRUNCATION_MARKER.encode("utf-8")
    if allowance < len(marker):
        return None, 0, True
    prefix = encoded[: allowance - len(marker)].decode("utf-8", errors="ignore")
    rendered = prefix + OUTPUT_TRUNCATION_MARKER
    return rendered, len(rendered.encode("utf-8")), True


def _projected_submission_results(
    db: Session, submission_id: int, viewer_is_admin: bool
):
    visible_result = (
        TestResult.id.is_not(None)
        if viewer_is_admin
        else TestResult.is_hidden.is_(False)
    )
    visible_position = (
        func.row_number().over(order_by=TestResult.id)
        if viewer_is_admin
        else func.sum(sql_case((visible_result, 1), else_=0)).over(
            order_by=TestResult.id
        )
    )
    output_is_projected = visible_result & (
        visible_position <= MAX_PROJECTED_OUTPUT_ROWS
    )

    def projected_output(column):
        return sql_case(
            (
                output_is_projected,
                func.substr(column, 1, MAX_PROJECTED_OUTPUT_CHARS),
            ),
            else_=None,
        )

    def projected_output_overflow(column):
        return sql_case(
            (
                output_is_projected,
                func.substr(column, MAX_PROJECTED_OUTPUT_CHARS + 1, 1) != "",
            ),
            else_=None,
        )

    projected_message = sql_case(
        (
            visible_result,
            func.substr(TestResult.message, 1, MAX_PROJECTED_MESSAGE_CHARS),
        ),
        else_=None,
    )
    projected_message_overflow = sql_case(
        (
            visible_result,
            func.substr(TestResult.message, MAX_PROJECTED_MESSAGE_CHARS + 1, 1) != "",
        ),
        else_=None,
    )

    rows = db.execute(
        select(
            TestResult.id,
            TestResult.name,
            TestResult.status,
            TestResult.score,
            TestResult.max_score,
            TestResult.runtime_ms,
            projected_message.label("message"),
            projected_message_overflow.label("message_overflow"),
            output_is_projected.label("output_projected"),
            projected_output(TestResult.actual_output).label("actual_output"),
            projected_output_overflow(TestResult.actual_output).label(
                "actual_output_overflow"
            ),
            projected_output(TestResult.expected_output).label("expected_output"),
            projected_output_overflow(TestResult.expected_output).label(
                "expected_output_overflow"
            ),
            TestResult.is_hidden,
            TestResult.created_at,
        )
        .where(TestResult.submission_id == submission_id)
        .order_by(TestResult.id)
        .limit(MAX_PROJECTED_RESULTS + 1)
    ).all()
    return rows[:MAX_PROJECTED_RESULTS], len(rows) > MAX_PROJECTED_RESULTS


def _projected_submission_text(db: Session, submission_id: int):
    return db.execute(
        select(
            func.substr(Submission.code, 1, MAX_PROJECTED_CODE_CHARS).label("code"),
            (func.substr(Submission.code, MAX_PROJECTED_CODE_CHARS + 1, 1) != "").label(
                "code_overflow"
            ),
            func.substr(
                Submission.error_message,
                1,
                MAX_PROJECTED_ERROR_MESSAGE_CHARS,
            ).label("error_message"),
            (
                func.substr(
                    Submission.error_message,
                    MAX_PROJECTED_ERROR_MESSAGE_CHARS + 1,
                    1,
                )
                != ""
            ).label("error_message_overflow"),
        ).where(Submission.id == submission_id)
    ).one()


def _mark_projected_text(
    value: Optional[str], overflow: Optional[bool], limit: int
) -> Optional[str]:
    if value is None or not overflow:
        return value
    prefix_chars = max(0, limit - len(OUTPUT_TRUNCATION_MARKER))
    return value[:prefix_chars] + OUTPUT_TRUNCATION_MARKER


def submission_to_read(
    db: Optional[Session], submission: Submission, viewer_is_admin: bool = False
):
    from app.schemas import SubmissionRead, TestResultRead

    results = []
    visible_output_bytes_remaining = MAX_VISIBLE_OUTPUT_TOTAL_BYTES
    if db is not None:
        projected_submission = _projected_submission_text(db, submission.id)
        submission_code = _mark_projected_text(
            projected_submission.code,
            projected_submission.code_overflow,
            MAX_PROJECTED_CODE_CHARS,
        )
        submission_error_message = _mark_projected_text(
            projected_submission.error_message,
            projected_submission.error_message_overflow,
            MAX_PROJECTED_ERROR_MESSAGE_CHARS,
        )
        source_results, additional_results_omitted = _projected_submission_results(
            db,
            submission.id,
            viewer_is_admin,
        )
    else:
        submission_code = submission.code
        submission_error_message = submission.error_message
        source_results = submission.results
        additional_results_omitted = False
    for result_index, result in enumerate(source_results):
        hide_detail = result.is_hidden and not viewer_is_admin
        actual_output = None
        expected_output = None
        output_limited = False
        if not hide_detail:
            output_projected = getattr(result, "output_projected", True)
            projected_actual_limited = not output_projected or bool(
                getattr(result, "actual_output_overflow", False)
            )
            projected_expected_limited = not output_projected or bool(
                getattr(result, "expected_output_overflow", False)
            )
            actual_output, used, actual_limited = _bounded_visible_output(
                result.actual_output,
                visible_output_bytes_remaining,
            )
            visible_output_bytes_remaining -= used
            expected_output, used, expected_limited = _bounded_visible_output(
                result.expected_output,
                visible_output_bytes_remaining,
            )
            visible_output_bytes_remaining -= used
            output_limited = (
                projected_actual_limited
                or projected_expected_limited
                or actual_limited
                or expected_limited
            )

        message = (
            ("Accepted" if result.status == "AC" else "Hidden test case")
            if hide_detail
            else result.message
        )
        if not hide_detail and bool(getattr(result, "message_overflow", False)):
            message = f"{message or ''}... (message truncated)".strip()
        if output_limited:
            message = f"{message or ''} (output truncated for response size)".strip()
        if additional_results_omitted and result_index == len(source_results) - 1:
            message = f"{message or ''} (additional legacy results omitted)".strip()
        results.append(
            TestResultRead(
                id=result.id,
                name="Hidden test case" if hide_detail else result.name,
                status=result.status,
                score=result.score,
                max_score=result.max_score,
                runtime_ms=0 if hide_detail else result.runtime_ms,
                message=message,
                actual_output=actual_output,
                expected_output=expected_output,
                is_hidden=result.is_hidden,
                created_at=result.created_at,
            )
        )

    return SubmissionRead(
        id=submission.id,
        user_id=submission.user_id,
        problem_id=submission.problem_id,
        problem_slug=submission.problem.slug,
        problem_title=submission.problem.title,
        username=submission.user.username,
        code=submission_code,
        mode=submission.mode,
        status=submission.status,
        score=submission.score,
        max_score=submission.max_score,
        error_message=submission_error_message,
        created_at=submission.created_at,
        completed_at=submission.completed_at,
        results=results,
    )
