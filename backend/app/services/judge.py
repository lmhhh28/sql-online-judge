from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import psycopg
from psycopg import errors, sql
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Problem, Submission, TestCase, TestResult
from app.services.comparator import compare_csv_tables, compare_string, rows_to_csv
from app.services.sql_guard import UnsafeSqlError, validate_user_sql


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
    return conninfo.replace("/postgres?", f"/{database}?", 1)


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
    run_db = f"oj_run_{submission.id}_{case.id}_{uuid.uuid4().hex[:8]}"[:60]
    started = time.perf_counter()
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
                return CaseOutcome("SE", 0.0, _elapsed_ms(started), f"setup.sql failed: {exc}")

            try:
                user_output = None
                if submission.code.strip():
                    cursor = conn.execute(submission.code)
                    if cursor.description:
                        columns = [desc.name for desc in cursor.description or []]
                        rows = cursor.fetchall()
                        user_output = rows_to_csv(columns, rows)
            except errors.QueryCanceled as exc:
                return CaseOutcome("TLE", 0.0, _elapsed_ms(started), str(exc))
            except errors.SyntaxError as exc:
                return CaseOutcome("CE", 0.0, _elapsed_ms(started), str(exc))
            except Exception as exc:
                return CaseOutcome("RE", 0.0, _elapsed_ms(started), str(exc))

            try:
                if case.judge_sql.strip() == "__USER_OUTPUT__":
                    if user_output is None:
                        return CaseOutcome("WA", 0.0, _elapsed_ms(started), "User SQL did not return a result set")
                    actual = user_output
                else:
                    cursor = conn.execute(case.judge_sql)
                    columns = [desc.name for desc in cursor.description or []]
                    rows = cursor.fetchall() if cursor.description else []
                    actual = rows_to_csv(columns, rows)
            except errors.QueryCanceled as exc:
                return CaseOutcome("TLE", 0.0, _elapsed_ms(started), str(exc))
            except Exception as exc:
                return CaseOutcome("SE", 0.0, _elapsed_ms(started), f"judge.sql failed: {exc}")

        if problem.checker == "string":
            compare = compare_string(actual, case.expected_output)
        else:
            compare = compare_csv_tables(actual, case.expected_output, ordered=problem.ordered)

        accepted = compare.accepted
        return CaseOutcome(
            "AC" if accepted else "WA",
            case.score if accepted else 0.0,
            _elapsed_ms(started),
            compare.message,
            actual,
        )
    except UnsafeSqlError as exc:
        return CaseOutcome("RE", 0.0, _elapsed_ms(started), f"Blocked SQL: {exc}")
    except errors.QueryCanceled as exc:
        return CaseOutcome("TLE", 0.0, _elapsed_ms(started), str(exc))
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
            db.refresh(submission)
            outcome = _run_sql_case(submission, problem, case)
            total_score += outcome.score
            if outcome.status != "AC" and final_status == "AC":
                final_status = outcome.status

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

            if submission.mode == "submit" and outcome.status in {"CE", "RE", "TLE", "SE"}:
                # Continue judging hidden cases is unnecessary for the first local version.
                pass

        submission.score = total_score
        submission.max_score = max_score
        submission.status = final_status
        submission.error_message = None if final_status == "AC" else "Some test cases failed"
        submission.completed_at = datetime.utcnow()
        db.commit()
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
