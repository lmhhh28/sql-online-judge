from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg import errors, sql
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Problem, Submission, TestCase, TestResult
from app.services.comparator import compare_csv_tables, compare_string, rows_to_csv
from app.services.sql_guard import UnsafeSqlError, validate_user_sql

RUN_DB_PREFIX = "oj_run_"
USER_OUTPUT_SENTINEL = "__USER_OUTPUT__"
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


def _psycopg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def _admin_conninfo(database: Optional[str] = None) -> str:
    conninfo = _psycopg_url(settings.admin_database_url)
    if database is None:
        return conninfo
    params = conninfo_to_dict(conninfo)
    params["dbname"] = database
    return make_conninfo(**params)


def _merge_status(current: str, candidate: str) -> str:
    if STATUS_PRIORITY.get(candidate, 0) > STATUS_PRIORITY.get(current, 0):
        return candidate
    return current


def _cursor_to_csv(cursor) -> str:
    columns = [desc.name for desc in cursor.description or []]
    rows = cursor.fetchall() if cursor.description else []
    return rows_to_csv(columns, rows)


def cleanup_stale_run_databases() -> int:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        db_names = [
            row[0]
            for row in conn.execute(
                "SELECT datname FROM pg_database WHERE datname LIKE %s",
                (f"{RUN_DB_PREFIX}%",),
            )
        ]

    dropped = 0
    for db_name in db_names:
        try:
            _drop_run_database(db_name)
            dropped += 1
        except Exception:
            continue
    return dropped


def recover_interrupted_judges() -> tuple[int, int]:
    dropped = cleanup_stale_run_databases()
    db = SessionLocal()
    try:
        interrupted = list(db.scalars(select(Submission).where(Submission.status.in_(("PENDING", "RUNNING")))))
        now = datetime.utcnow()
        for submission in interrupted:
            submission.status = "SE"
            submission.error_message = "Judge interrupted before completion"
            submission.completed_at = now
        db.commit()
        return len(interrupted), dropped
    finally:
        db.close()


def _create_run_database(db_name: str) -> None:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
                sql.Identifier(db_name),
                sql.Identifier(settings.judge_runner_role),
            )
        )


def _drop_run_database(db_name: str) -> None:
    with psycopg.connect(_admin_conninfo(), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s",
            (db_name,),
        )
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name)))


def _run_sql_case(submission: Submission, problem: Problem, case: TestCase) -> CaseOutcome:
    run_db = f"{RUN_DB_PREFIX}{submission.id}_{case.id}_{uuid.uuid4().hex[:8]}"[:60]
    started = time.perf_counter()
    user_runtime_ms = 0
    try:
        validate_user_sql(submission.code)
        _create_run_database(run_db)
        with psycopg.connect(_admin_conninfo(run_db), autocommit=True) as conn:
            timeout_ms = int(problem.time_limit_ms)
            conn.execute(f"SET statement_timeout TO {timeout_ms}")
            conn.execute(f"SET lock_timeout TO {timeout_ms}")
            conn.execute(f"SET idle_in_transaction_session_timeout TO {timeout_ms}")
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(settings.judge_runner_role)))

            try:
                if case.setup_sql.strip():
                    conn.execute(case.setup_sql)
            except Exception as exc:
                return CaseOutcome("SE", 0.0, 0, f"setup.sql failed: {exc}")

            try:
                user_output = None
                capture_user_output = case.judge_sql.strip() == USER_OUTPUT_SENTINEL
                if submission.code.strip():
                    user_started = time.perf_counter()
                    cursor = conn.execute(submission.code)
                    if capture_user_output:
                        result_sets = []
                        if cursor.description:
                            result_sets.append(_cursor_to_csv(cursor))
                        while cursor.nextset():
                            if cursor.description:
                                result_sets.append(_cursor_to_csv(cursor))
                        if len(result_sets) > 1:
                            user_runtime_ms = _elapsed_ms(user_started)
                            return CaseOutcome(
                                "WA",
                                0.0,
                                user_runtime_ms,
                                "User SQL returned multiple result sets; return exactly one result set",
                            )
                        if result_sets:
                            user_output = result_sets[0]
                    user_runtime_ms = _elapsed_ms(user_started)
            except errors.QueryCanceled as exc:
                return CaseOutcome("TLE", 0.0, user_runtime_ms or _elapsed_ms(user_started), str(exc))
            except errors.SyntaxError as exc:
                return CaseOutcome("CE", 0.0, user_runtime_ms or _elapsed_ms(user_started), str(exc))
            except Exception as exc:
                return CaseOutcome("RE", 0.0, user_runtime_ms or _elapsed_ms(user_started), str(exc))

            try:
                if case.judge_sql.strip() == USER_OUTPUT_SENTINEL:
                    if user_output is None:
                        return CaseOutcome("WA", 0.0, user_runtime_ms, "User SQL did not return a result set")
                    actual = user_output
                else:
                    cursor = conn.execute(case.judge_sql)
                    columns = [desc.name for desc in cursor.description or []]
                    rows = cursor.fetchall() if cursor.description else []
                    actual = rows_to_csv(columns, rows)
            except errors.QueryCanceled as exc:
                return CaseOutcome("TLE", 0.0, user_runtime_ms, str(exc))
            except Exception as exc:
                return CaseOutcome("SE", 0.0, user_runtime_ms, f"judge.sql failed: {exc}")

        if problem.checker == "string":
            compare = compare_string(actual, case.expected_output)
        else:
            compare = compare_csv_tables(actual, case.expected_output, ordered=problem.ordered)

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
    except errors.QueryCanceled as exc:
        return CaseOutcome("TLE", 0.0, user_runtime_ms, str(exc))
    except Exception as exc:
        return CaseOutcome("SE", 0.0, _elapsed_ms(started), str(exc))
    finally:
        try:
            _drop_run_database(run_db)
        except Exception:
            pass


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def judge_submission(submission_id: int) -> None:
    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return

        problem = submission.problem
        submission.status = "RUNNING"
        db.commit()

        case_query = select(TestCase).where(TestCase.problem_id == problem.id).order_by(TestCase.id)
        if submission.mode == "run":
            case_query = case_query.where(TestCase.is_hidden.is_(False))
        cases = list(db.scalars(case_query))

        if not cases:
            submission.status = "SE"
            submission.error_message = "No test cases selected"
            submission.completed_at = datetime.utcnow()
            db.commit()
            return

        max_score = sum(case.score for case in cases)
        total_score = 0.0
        final_status = "AC"

        for case in cases:
            outcome = _run_sql_case(submission, problem, case)
            total_score += outcome.score
            final_status = _merge_status(final_status, outcome.status)

            db.add(
                TestResult(
                    submission_id=submission.id,
                    test_case_id=case.id,
                    name=case.name,
                    status=outcome.status,
                    score=outcome.score,
                    max_score=case.score,
                    runtime_ms=outcome.runtime_ms,
                    message=outcome.message,
                    actual_output=outcome.actual_output,
                    expected_output=case.expected_output,
                    is_hidden=case.is_hidden,
                )
            )
            db.commit()

            if submission.mode == "submit" and outcome.status in TERMINAL_ERROR_STATUSES:
                break

        submission.score = total_score
        submission.max_score = max_score
        submission.status = final_status
        submission.error_message = None if final_status == "AC" else "Some test cases failed"
        submission.completed_at = datetime.utcnow()
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            submission = db.get(Submission, submission_id)
            if submission is not None:
                submission.status = "SE"
                submission.error_message = f"Judge failed: {exc}"
                submission.completed_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def submission_to_read(db: Session, submission: Submission, viewer_is_admin: bool = False):
    from app.schemas import SubmissionRead, TestResultRead

    results = []
    for result in submission.results:
        hide_detail = result.is_hidden and not viewer_is_admin
        results.append(
            TestResultRead(
                id=result.id,
                name=result.name,
                status=result.status,
                score=result.score,
                max_score=result.max_score,
                runtime_ms=result.runtime_ms,
                message="Hidden test case" if hide_detail and result.status != "AC" else result.message,
                actual_output=None if hide_detail else result.actual_output,
                expected_output=None if hide_detail else result.expected_output,
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
        code=submission.code,
        mode=submission.mode,
        status=submission.status,
        score=submission.score,
        max_score=submission.max_score,
        error_message=submission.error_message,
        created_at=submission.created_at,
        completed_at=submission.completed_at,
        results=results,
    )
