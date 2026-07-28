import threading
import time
import uuid
from concurrent.futures import Future
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import defer, selectinload, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Problem, Submission, User, utc_now
from app.models import TestCase as CaseModel
from app.models import TestResult as ResultModel
from app.services import judge
from app.services.judge import (
    MAX_OUTPUT_BYTES,
    DatabaseSizeExceeded,
    OutputLimitExceeded,
    SqlDeadlineExceeded,
    _admin_conninfo,
    _consume_result_sets,
    _create_run_database,
    _create_run_role,
    _disable_run_role_login,
    _drop_run_database,
    _drop_run_role,
    _effective_timeout_ms,
    _execute_user_sql,
    _merge_status,
    _query_deadline,
    _runner_conninfo,
    _valid_run_database_name,
    _valid_run_role_name,
    judge_submission,
    shutdown_judge_executor,
    start_judge_executor,
    submission_to_read,
)
from app.services.sql_guard import UnsafeSqlError, validate_user_sql


def test_admin_conninfo_replaces_database_without_query_string(monkeypatch):
    monkeypatch.setattr(
        settings,
        "admin_database_url",
        "postgresql+psycopg://user:secret@localhost/postgres",
    )

    params = conninfo_to_dict(_admin_conninfo("oj_run_test"))

    assert params["dbname"] == "oj_run_test"
    assert params["user"] == "user"
    assert params["password"] == "secret"
    assert params["host"] == "localhost"


def test_admin_conninfo_enforces_bounded_connection_timeout(monkeypatch):
    monkeypatch.setattr(
        settings,
        "admin_database_url",
        "postgresql+psycopg://admin@localhost/postgres?connect_timeout=9999",
    )

    params = conninfo_to_dict(_admin_conninfo())

    assert params["connect_timeout"] == "5"


def test_admin_conninfo_preserves_unix_socket_host(monkeypatch):
    monkeypatch.setattr(
        settings, "admin_database_url", "postgresql+psycopg://lmhhh@/postgres?host=/tmp"
    )

    params = conninfo_to_dict(_admin_conninfo("oj_run_test"))

    assert params["dbname"] == "oj_run_test"
    assert params["user"] == "lmhhh"
    assert params["host"] == "/tmp"


def test_merge_status_keeps_most_severe_status():
    status = "AC"
    for candidate in ["WA", "AC", "RE", "TLE", "CE"]:
        status = _merge_status(status, candidate)

    assert status == "TLE"
    assert _merge_status("WA", "SE") == "SE"


def test_runner_conninfo_replaces_admin_identity(monkeypatch):
    monkeypatch.setattr(
        settings,
        "admin_database_url",
        "postgresql+psycopg://admin:secret@localhost/postgres",
    )

    params = conninfo_to_dict(
        _runner_conninfo("oj_run_" + "a" * 32, "oj_exec_" + "a" * 32, "random")
    )

    assert params["dbname"] == "oj_run_" + "a" * 32
    assert params["user"] == "oj_exec_" + "a" * 32
    assert params["password"] == "random"
    assert params["connect_timeout"] == "5"


def test_run_resource_names_are_strictly_scoped():
    assert _valid_run_database_name("oj_run_" + "a" * 32)
    assert _valid_run_database_name("oj_run_1_2_deadbeef")
    assert not _valid_run_database_name("ojXrunXunrelated")
    assert not _valid_run_database_name("oj_run_postgres")
    assert _valid_run_role_name("oj_exec_" + "f" * 32)
    assert not _valid_run_role_name("oj_exec_admin")


def test_drop_helpers_reject_configured_database_and_role_names(monkeypatch):
    protected_database = "oj_run_" + "a" * 32
    protected_role = "oj_exec_" + "b" * 32
    monkeypatch.setattr(
        settings,
        "app_database_url",
        f"postgresql+psycopg://{protected_role}@localhost/{protected_database}",
    )
    connect_calls = []
    monkeypatch.setattr(
        judge.psycopg, "connect", lambda *_args, **_kwargs: connect_calls.append(True)
    )

    with pytest.raises(ValueError, match="configured application"):
        _drop_run_database(protected_database)
    with pytest.raises(ValueError, match="configured database role"):
        _drop_run_role(protected_role)

    assert connect_calls == []


def test_stale_cleanup_excludes_all_configured_and_connected_targets(monkeypatch):
    app_database = "oj_run_" + "a" * 32
    admin_database = "oj_run_" + "b" * 32
    stale_database = "oj_run_" + "c" * 32
    mismatched_database = "oj_run_" + "7" * 32
    owned_legacy_database = "oj_run_1_2_deadbeef"
    unrelated_legacy_database = "oj_run_1_3_feedface"
    runner_role = "oj_exec_" + "d" * 32
    app_role = "oj_exec_" + "e" * 32
    admin_role = "oj_exec_" + "f" * 32
    stale_role = "oj_exec_" + "c" * 32
    mismatched_role = "oj_exec_" + "7" * 32
    orphan_role = "oj_exec_" + "8" * 32
    elevated_orphan_role = "oj_exec_" + "9" * 32
    unmarked_database = "oj_run_" + "1" * 32
    unmarked_role = "oj_exec_" + "1" * 32
    unmarked_orphan_role = "oj_exec_" + "2" * 32
    monkeypatch.setattr(
        settings,
        "app_database_url",
        f"postgresql+psycopg://{app_role}@localhost/{app_database}",
    )
    monkeypatch.setattr(
        settings,
        "admin_database_url",
        f"postgresql+psycopg://{admin_role}@localhost/{admin_database}",
    )
    monkeypatch.setattr(settings, "judge_runner_role", runner_role)

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0]

        def __iter__(self):
            return iter(self.rows)

    def role_record(role_name, *, is_superuser=False, comment=judge.RUN_ROLE_COMMENT):
        return (
            role_name,
            False,
            is_superuser,
            False,
            False,
            False,
            False,
            False,
            1,
            False,
            comment,
        )

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, _parameters=None):
            if "current_database(), current_user" in statement:
                return Result([(admin_database, admin_role, admin_role)])
            if "FROM pg_database" in statement:
                return Result(
                    [
                        (app_database, app_role, None),
                        (admin_database, admin_role, None),
                        (stale_database, stale_role, judge.RUN_DB_COMMENT),
                        (unmarked_database, unmarked_role, None),
                        (mismatched_database, app_role, None),
                        (owned_legacy_database, runner_role, None),
                        (unrelated_legacy_database, app_role, None),
                    ]
                )
            if "FROM pg_roles role" in statement:
                return Result(
                    [
                        role_record(runner_role),
                        role_record(app_role),
                        role_record(admin_role),
                        role_record(stale_role),
                        role_record(mismatched_role),
                        role_record(orphan_role),
                        role_record(unmarked_role, comment=None),
                        role_record(unmarked_orphan_role, comment=None),
                        role_record(elevated_orphan_role, is_superuser=True),
                    ]
                )
            raise AssertionError(statement)

    dropped_databases = []
    dropped_roles = []
    monkeypatch.setattr(
        judge.psycopg, "connect", lambda *_args, **_kwargs: FakeConnection()
    )
    monkeypatch.setattr(judge, "_drop_run_database", dropped_databases.append)
    monkeypatch.setattr(judge, "_drop_run_role", dropped_roles.append)

    assert judge.cleanup_stale_run_databases() == 2
    assert dropped_databases == [stale_database, owned_legacy_database]
    assert dropped_roles == [stale_role, orphan_role]


def test_recovery_commits_submission_state_before_cluster_cleanup(monkeypatch):
    submissions = [
        SimpleNamespace(status="PENDING", error_message=None, completed_at=None),
        SimpleNamespace(status="RUNNING", error_message=None, completed_at=None),
    ]
    events = []

    class FakeSession:
        def scalars(self, _statement):
            return submissions

        def commit(self):
            events.append("commit")

        def close(self):
            events.append("close")

    def failed_cleanup():
        events.append("cleanup")
        raise RuntimeError("administrator connection unavailable")

    monkeypatch.setattr(judge, "SessionLocal", FakeSession)
    monkeypatch.setattr(judge, "cleanup_stale_run_databases", failed_cleanup)

    with pytest.raises(RuntimeError, match="administrator connection unavailable"):
        judge.recover_interrupted_judges()

    assert events == ["commit", "close", "cleanup"]
    assert all(submission.status == "SE" for submission in submissions)
    assert all(submission.completed_at is not None for submission in submissions)


def test_effective_timeout_is_bounded():
    assert _effective_timeout_ms(0) == judge.MIN_TIME_LIMIT_MS
    assert _effective_timeout_ms(500) == 500
    assert _effective_timeout_ms(999_999) == judge.MAX_TIME_LIMIT_MS
    assert _effective_timeout_ms("invalid") == judge.MIN_TIME_LIMIT_MS


def test_submission_deadline_helpers_and_zero_budget_case(monkeypatch):
    monkeypatch.setattr(judge, "MAX_SUBMISSION_WALL_TIME_MS", 10)
    assert judge._remaining_submission_budget_ms(time.monotonic() - 1) == 0

    phase_timeout_ms, capped = judge._phase_timeout_ms(1_000, time.monotonic() + 0.05)
    assert 1 <= phase_timeout_ms <= 50
    assert capped
    with pytest.raises(judge.SubmissionDeadlineExceeded):
        judge._phase_timeout_ms(100, time.monotonic() - 1)

    create_calls = []
    monkeypatch.setattr(judge, "_judge_shutdown_requested", threading.Event())
    monkeypatch.setattr(
        judge, "_create_run_role", lambda *args: create_calls.append(args)
    )
    outcome = judge._run_sql_case(
        SimpleNamespace(id=1, code="SELECT 1"),
        SimpleNamespace(time_limit_ms=100, checker="result", ordered=False),
        SimpleNamespace(
            id=1,
            setup_sql="",
            judge_sql=judge.USER_OUTPUT_SENTINEL,
            expected_output="?column?\n1\n",
            score=1.0,
        ),
        total_time_limit_ms=0,
    )

    assert outcome.status == "TLE"
    assert outcome.message == "Submission time limit exceeded"
    assert create_calls == []


def test_stored_output_budget_reserves_space_for_public_and_hidden_cases(monkeypatch):
    monkeypatch.setattr(judge, "MAX_VISIBLE_OUTPUT_TOTAL_BYTES", 101)

    mixed = judge._stored_output_budgets(
        [SimpleNamespace(is_hidden=True), SimpleNamespace(is_hidden=False)]
    )
    public_only = judge._stored_output_budgets([SimpleNamespace(is_hidden=False)])

    assert mixed[False] > 0
    assert mixed[True] > 0
    assert sum(mixed.values()) == 101
    assert public_only == {False: 101, True: 0}


class _FakeCursor:
    def __init__(self, rows):
        self.description = [SimpleNamespace(name="value")]
        self._rows = list(rows)
        self._position = 0
        self.fetch_sizes = []

    def fetchmany(self, size):
        self.fetch_sizes.append(size)
        rows = self._rows[self._position : self._position + size]
        self._position += len(rows)
        return rows

    def nextset(self):
        return None


def test_cursor_output_is_incremental_and_preserves_null(monkeypatch):
    monkeypatch.setattr(judge, "MAX_OUTPUT_ROWS", 2)
    cursor = _FakeCursor([(None,), ("NULL",)])

    count, output = _consume_result_sets(cursor, collect_first=True)

    assert count == 1
    assert output == "value\n\\N\nNULL\n"
    assert set(cursor.fetch_sizes) == {1}


def test_cursor_output_enforces_row_and_byte_limits(monkeypatch):
    assert judge.MAX_OUTPUT_CELL_BYTES == MAX_OUTPUT_BYTES
    monkeypatch.setattr(judge, "MAX_OUTPUT_ROWS", 1)
    with pytest.raises(OutputLimitExceeded):
        _consume_result_sets(_FakeCursor([(1,), (2,)]), collect_first=True)

    monkeypatch.setattr(judge, "MAX_OUTPUT_ROWS", 10)
    monkeypatch.setattr(judge, "MAX_OUTPUT_BYTES", 10)
    with pytest.raises(OutputLimitExceeded):
        _consume_result_sets(
            _FakeCursor([("x" * MAX_OUTPUT_BYTES,)]), collect_first=True
        )

    monkeypatch.setattr(judge, "MAX_OUTPUT_BYTES", MAX_OUTPUT_BYTES)
    monkeypatch.setattr(judge, "MAX_OUTPUT_CELL_BYTES", 3)
    with pytest.raises(OutputLimitExceeded):
        _consume_result_sets(_FakeCursor([("four",)]), collect_first=True)


def test_query_deadline_cancels_and_reports_timeout(monkeypatch):
    cancelled = threading.Event()
    terminated = []

    class FakeConnection:
        info = SimpleNamespace(backend_pid=123)

        def cancel_safe(self, timeout):
            cancelled.set()

    monkeypatch.setattr(
        judge, "_terminate_run_backend", lambda *args: terminated.append(args)
    )

    with pytest.raises(SqlDeadlineExceeded):
        with _query_deadline(
            FakeConnection(),
            10,
            "oj_run_" + "a" * 32,
            "oj_exec_" + "a" * 32,
            monitor_database_size=False,
        ):
            assert cancelled.wait(1)

    assert not terminated


def test_query_deadline_timer_does_not_cancel_after_phase_finished(monkeypatch):
    cancelled = threading.Event()

    class FakeConnection:
        info = SimpleNamespace(backend_pid=123)

        def cancel_safe(self, timeout):
            cancelled.set()

    class EdgeTimer:
        daemon = False

        def __init__(self, _interval, callback):
            self.callback = callback

        def start(self):
            pass

        def cancel(self):
            # Model a timer callback dequeued exactly as the context exits.
            self.callback()

        def join(self, timeout):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(judge.threading, "Timer", EdgeTimer)

    with _query_deadline(
        FakeConnection(),
        1_000,
        "oj_run_" + "a" * 32,
        "oj_exec_" + "a" * 32,
        monitor_database_size=False,
    ):
        pass

    assert not cancelled.is_set()


def test_query_deadline_fails_closed_when_resource_watchdog_breaks(monkeypatch):
    cancelled = threading.Event()

    class FakeConnection:
        info = SimpleNamespace(backend_pid=123)

        def cancel_safe(self, timeout):
            cancelled.set()

    def fail_to_monitor(*_args, **_kwargs):
        raise psycopg.OperationalError("monitor unavailable")

    monkeypatch.setattr(judge.psycopg, "connect", fail_to_monitor)
    monkeypatch.setattr(judge, "_terminate_run_backend", lambda *_args: None)

    with pytest.raises(judge.ResourceWatchdogFailed):
        with _query_deadline(
            FakeConnection(),
            1_000,
            "oj_run_" + "a" * 32,
            "oj_exec_" + "a" * 32,
        ):
            assert cancelled.wait(1)


def test_query_deadline_checks_size_even_when_query_finishes_immediately(monkeypatch):
    checked = threading.Event()

    class FakeRunnerConnection:
        info = SimpleNamespace(backend_pid=123)

        def cancel_safe(self, timeout):
            raise AssertionError("a completed query must not be cancelled")

    class SizeResult:
        def fetchone(self):
            checked.set()
            return (judge.MAX_RUN_DATABASE_BYTES + 1,)

    class FakeAdminConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, _parameters=None):
            return SizeResult() if "pg_database_size" in statement else self

    monkeypatch.setattr(
        judge.psycopg,
        "connect",
        lambda *_args, **_kwargs: FakeAdminConnection(),
    )

    with pytest.raises(DatabaseSizeExceeded):
        with _query_deadline(
            FakeRunnerConnection(),
            1_000,
            "oj_run_" + "a" * 32,
            "oj_exec_" + "a" * 32,
        ):
            pass

    assert checked.is_set()


def test_query_deadline_performs_a_final_size_check_after_completion(monkeypatch):
    first_check = threading.Event()
    checked_sizes = iter([0, judge.MAX_RUN_DATABASE_BYTES + 1])

    class FakeRunnerConnection:
        info = SimpleNamespace(backend_pid=123)

        def cancel_safe(self, timeout):
            raise AssertionError("a completed query must not be cancelled")

    class SizeResult:
        def fetchone(self):
            size = next(checked_sizes)
            first_check.set()
            return (size,)

    class FakeAdminConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, _parameters=None):
            return SizeResult() if "pg_database_size" in statement else self

    monkeypatch.setattr(
        judge.psycopg,
        "connect",
        lambda *_args, **_kwargs: FakeAdminConnection(),
    )

    with pytest.raises(DatabaseSizeExceeded):
        with _query_deadline(
            FakeRunnerConnection(),
            1_000,
            "oj_run_" + "a" * 32,
            "oj_exec_" + "a" * 32,
        ):
            assert first_check.wait(1)


def test_query_deadline_closes_connection_when_a_watchdog_will_not_stop(monkeypatch):
    closed = threading.Event()

    class FakeConnection:
        info = SimpleNamespace(backend_pid=123)

        def close(self):
            closed.set()

    class StuckWatchdog:
        daemon = False

        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def cancel(self):
            pass

        def join(self, timeout):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(judge.threading, "Timer", StuckWatchdog)
    monkeypatch.setattr(judge.threading, "Thread", StuckWatchdog)

    with pytest.raises(judge.ResourceWatchdogFailed):
        with _query_deadline(
            FakeConnection(),
            1_000,
            "oj_run_" + "a" * 32,
            "oj_exec_" + "a" * 32,
        ):
            pass

    assert closed.is_set()


@pytest.mark.parametrize(
    ("failure_call", "setup_sql", "judge_sql"),
    [
        (1, "CREATE TABLE input(value int)", judge.USER_OUTPUT_SENTINEL),
        (1, "", judge.USER_OUTPUT_SENTINEL),
        (2, "", "SELECT 1 AS value"),
    ],
)
def test_resource_watchdog_failures_are_system_errors(
    monkeypatch,
    failure_call,
    setup_sql,
    judge_sql,
):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _statement):
            return None

    deadline_calls = 0

    @contextmanager
    def injected_deadline(*_args, **_kwargs):
        nonlocal deadline_calls
        deadline_calls += 1
        if deadline_calls == failure_call:
            raise judge.ResourceWatchdogFailed
        yield

    monkeypatch.setattr(judge, "_judge_shutdown_requested", threading.Event())
    monkeypatch.setattr(judge, "_create_run_role", lambda *_args: None)
    monkeypatch.setattr(judge, "_create_run_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(judge, "_disable_run_role_login", lambda *_args: None)
    monkeypatch.setattr(judge, "_drop_run_database", lambda *_args: None)
    monkeypatch.setattr(judge, "_drop_run_role", lambda *_args: None)
    monkeypatch.setattr(judge, "_set_session_limits", lambda *_args: None)
    monkeypatch.setattr(judge, "_query_deadline", injected_deadline)
    monkeypatch.setattr(
        judge, "_execute_user_sql", lambda *_args, **_kwargs: (1, "value\n1\n")
    )
    monkeypatch.setattr(
        judge.psycopg, "connect", lambda *_args, **_kwargs: FakeConnection()
    )

    outcome = judge._run_sql_case(
        SimpleNamespace(id=1, code="SELECT 1 AS value"),
        SimpleNamespace(time_limit_ms=100, checker="result", ordered=False),
        SimpleNamespace(
            id=1,
            setup_sql=setup_sql,
            judge_sql=judge_sql,
            expected_output="value\n1\n",
            score=1.0,
        ),
    )

    assert outcome.status == "SE"
    assert "monitoring failed" in outcome.message


def test_setup_results_are_streamed_and_output_limit_is_a_system_error(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    @contextmanager
    def deadline(*_args, **_kwargs):
        yield

    execute_calls = []

    def limited_execute(_conn, sql, collect_first):
        execute_calls.append((sql, collect_first))
        raise OutputLimitExceeded

    monkeypatch.setattr(judge, "_judge_shutdown_requested", threading.Event())
    monkeypatch.setattr(judge, "_create_run_role", lambda *_args: None)
    monkeypatch.setattr(judge, "_create_run_database", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(judge, "_disable_run_role_login", lambda *_args: None)
    monkeypatch.setattr(judge, "_drop_run_database", lambda *_args: None)
    monkeypatch.setattr(judge, "_drop_run_role", lambda *_args: None)
    monkeypatch.setattr(judge, "_set_session_limits", lambda *_args: None)
    monkeypatch.setattr(judge, "_query_deadline", deadline)
    monkeypatch.setattr(judge, "_execute_user_sql", limited_execute)
    monkeypatch.setattr(
        judge.psycopg, "connect", lambda *_args, **_kwargs: FakeConnection()
    )

    outcome = judge._run_sql_case(
        SimpleNamespace(id=1, code="SELECT 1"),
        SimpleNamespace(time_limit_ms=100, checker="result", ordered=False),
        SimpleNamespace(
            id=1,
            setup_sql="SELECT generate_series(1, 1000000)",
            judge_sql=judge.USER_OUTPUT_SENTINEL,
            expected_output="value\n1\n",
            score=1.0,
        ),
    )

    assert execute_calls == [("SELECT generate_series(1, 1000000)", False)]
    assert outcome.status == "SE"
    assert outcome.message == "Test setup output exceeded the configured limit"


def test_judge_queue_rejects_without_blocking_shared_threadpool(monkeypatch):
    calls = []

    class FullCapacity:
        def acquire(self, blocking):
            calls.append(("acquire", blocking))
            return False

    monkeypatch.setattr(judge, "_judge_capacity", FullCapacity())
    monkeypatch.setattr(
        judge,
        "_mark_submission_overloaded",
        lambda submission_id: calls.append(("reject", submission_id)),
    )

    judge_submission(42)

    assert calls == [("acquire", False), ("reject", 42)]


def test_judge_queue_submits_to_dedicated_executor(monkeypatch):
    calls = []

    class Capacity:
        def acquire(self, blocking):
            calls.append(("acquire", blocking))
            return True

        def release(self):
            calls.append(("release",))

    class Executor:
        def submit(self, function, submission_id):
            calls.append(("submit", function, submission_id))
            future = Future()
            future.set_result(None)
            return future

    monkeypatch.setattr(judge, "_judge_capacity", Capacity())
    monkeypatch.setattr(judge, "_judge_executor", Executor())
    monkeypatch.setattr(judge, "_judge_accepting", True)

    judge_submission(43)

    assert calls[0] == ("acquire", False)
    assert calls[1][0] == "submit"
    assert calls[1][2] == 43
    assert calls[2] == ("release",)


def test_judge_executor_lifecycle_is_restartable(monkeypatch):
    calls = []

    class Executor:
        def shutdown(self, wait, cancel_futures):
            calls.append((wait, cancel_futures))

    monkeypatch.setattr(judge, "_judge_executor", Executor())
    monkeypatch.setattr(judge, "_judge_accepting", True)
    try:
        shutdown_judge_executor(wait=True)
        assert calls == [(True, True)]
        assert judge._judge_executor is None
        assert judge._judge_shutdown_requested.is_set()

        start_judge_executor()
        assert judge._judge_accepting is True
        assert not judge._judge_shutdown_requested.is_set()
    finally:
        start_judge_executor()


def test_submission_wall_budget_covers_the_entire_case_loop(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    try:
        with testing_session() as db:
            user = User(username="budget-user", password_hash="hash", role="user")
            problem = Problem(
                slug="budget-problem",
                title="Budget",
                checker="result",
                ordered=False,
                time_limit_ms=100,
            )
            db.add_all([user, problem])
            db.flush()
            cases = [
                CaseModel(
                    problem_id=problem.id,
                    name=f"case-{index}",
                    is_hidden=False,
                    score=1.0,
                    setup_sql="",
                    judge_sql=judge.USER_OUTPUT_SENTINEL,
                    expected_output="e" * 100,
                )
                for index in range(2)
            ]
            db.add_all(cases)
            db.flush()
            submission = Submission(
                user_id=user.id,
                problem_id=problem.id,
                code="SELECT 1 AS value",
                mode="run",
                status="PENDING",
            )
            db.add(submission)
            db.commit()
            submission_id = submission.id
            first_case_id = cases[0].id

        remaining_budgets = iter([10, 0])
        run_calls = []
        monkeypatch.setattr(judge, "SessionLocal", testing_session)
        monkeypatch.setattr(judge, "MAX_VISIBLE_OUTPUT_FIELD_BYTES", 20)
        monkeypatch.setattr(judge, "MAX_VISIBLE_OUTPUT_TOTAL_BYTES", 30)
        monkeypatch.setattr(
            judge,
            "_remaining_submission_budget_ms",
            lambda _started: next(remaining_budgets),
        )

        def fake_run(_submission, _problem, case, total_time_limit_ms=None):
            run_calls.append((case.id, total_time_limit_ms))
            return judge.CaseOutcome("AC", case.score, 1, "Accepted", "value\n1\n")

        monkeypatch.setattr(judge, "_run_sql_case", fake_run)
        judge._judge_submission_impl(submission_id)

        with testing_session() as db:
            stored_submission = db.get(Submission, submission_id)
            stored_results = list(
                db.scalars(
                    select(ResultModel)
                    .where(ResultModel.submission_id == submission_id)
                    .order_by(ResultModel.id)
                )
            )

        assert run_calls == [(first_case_id, 10)]
        assert stored_submission.status == "TLE"
        assert stored_submission.score == 0
        assert len(stored_results) == 1
        assert stored_results[0].status == "TLE"
        assert stored_results[0].expected_output.endswith(
            judge.OUTPUT_TRUNCATION_MARKER
        )
        assert len(stored_results[0].expected_output.encode("utf-8")) <= 20
        assert "stored output truncated" in stored_results[0].message
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_submission_read_hides_all_hidden_output_metadata():
    created_at = utc_now()
    hidden_result = SimpleNamespace(
        id=1,
        name="hidden_secret_name",
        status="WA",
        score=0.0,
        max_score=1.0,
        runtime_ms=12,
        message="Result mismatch: expected secret",
        actual_output="secret actual",
        expected_output="secret expected",
        is_hidden=True,
        created_at=created_at,
    )
    submission = SimpleNamespace(
        id=1,
        user_id=1,
        problem_id=1,
        problem=SimpleNamespace(slug="p", title="Problem"),
        user=SimpleNamespace(username="user"),
        code="select 1",
        mode="submit",
        status="WA",
        score=0.0,
        max_score=1.0,
        error_message="Some test cases failed",
        created_at=created_at,
        completed_at=created_at,
        results=[hidden_result],
    )

    hidden = submission_to_read(None, submission, viewer_is_admin=False).results[0]
    admin = submission_to_read(None, submission, viewer_is_admin=True).results[0]

    assert hidden.name == "Hidden test case"
    assert hidden.message == "Hidden test case"
    assert hidden.runtime_ms == 0
    assert hidden.actual_output is None
    assert hidden.expected_output is None
    assert admin.name == "hidden_secret_name"
    assert admin.actual_output == "secret actual"
    assert admin.expected_output == "secret expected"
    assert admin.runtime_ms == 12


def test_submission_read_bounds_visible_output_bytes_without_splitting_utf8(
    monkeypatch,
):
    created_at = utc_now()
    monkeypatch.setattr(judge, "MAX_VISIBLE_OUTPUT_FIELD_BYTES", 20)
    monkeypatch.setattr(judge, "MAX_VISIBLE_OUTPUT_TOTAL_BYTES", 30)
    visible_results = [
        SimpleNamespace(
            id=index,
            name=f"case-{index}",
            status="WA",
            score=0.0,
            max_score=1.0,
            runtime_ms=1,
            message="Mismatch",
            actual_output="你" * 20,
            expected_output="e" * 20,
            is_hidden=False,
            created_at=created_at,
        )
        for index in range(1, 3)
    ]
    submission = SimpleNamespace(
        id=1,
        user_id=1,
        problem_id=1,
        problem=SimpleNamespace(slug="p", title="Problem"),
        user=SimpleNamespace(username="user"),
        code="select 1",
        mode="run",
        status="WA",
        score=0.0,
        max_score=2.0,
        error_message="Some test cases failed",
        created_at=created_at,
        completed_at=created_at,
        results=visible_results,
    )

    rendered = submission_to_read(None, submission)
    visible_values = [
        value
        for result in rendered.results
        for value in (result.actual_output, result.expected_output)
        if value is not None
    ]

    assert sum(len(value.encode("utf-8")) for value in visible_values) <= 30
    assert all(len(value.encode("utf-8")) <= 20 for value in visible_values)
    assert rendered.results[0].actual_output.endswith(judge.OUTPUT_TRUNCATION_MARKER)
    assert rendered.results[0].expected_output is None
    assert rendered.results[1].actual_output is None
    assert all(
        "output truncated for response size" in result.message
        for result in rendered.results
    )


def test_submission_read_projects_legacy_outputs_without_loading_relationship():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    try:
        with testing_session() as db:
            user = User(username="projection-user", password_hash="hash", role="user")
            problem = Problem(
                slug="projection-problem",
                title="Projection",
                checker="result",
                ordered=False,
                time_limit_ms=100,
            )
            db.add_all([user, problem])
            db.flush()
            submission = Submission(
                user_id=user.id,
                problem_id=problem.id,
                code="c" * 60_000,
                mode="run",
                status="WA",
                score=0.0,
                max_score=100.0,
                error_message="e" * 11_000,
            )
            db.add(submission)
            db.flush()
            submission_id = submission.id
            db.add_all(
                [
                    ResultModel(
                        submission_id=submission.id,
                        name=f"case-{index}",
                        status="WA",
                        score=0.0,
                        max_score=1.0,
                        runtime_ms=1,
                        message="m" * 11_000 if index == 9 else "Mismatch",
                        actual_output="x" * 20_000 if index < 19 else "x",
                        expected_output="y" * 20_000 if index < 19 else "y",
                        is_hidden=index < 9,
                    )
                    for index in range(101)
                ]
            )
            db.commit()
            db.expunge_all()

            stored_submission = db.scalar(
                select(Submission)
                .options(
                    defer(Submission.code, raiseload=True),
                    defer(Submission.error_message, raiseload=True),
                    selectinload(Submission.problem),
                    selectinload(Submission.user),
                )
                .where(Submission.id == submission_id)
            )
            assert "results" in inspect(stored_submission).unloaded
            assert "code" in inspect(stored_submission).unloaded
            assert "error_message" in inspect(stored_submission).unloaded

            rendered = submission_to_read(db, stored_submission)

            assert "results" in inspect(stored_submission).unloaded
            assert "code" in inspect(stored_submission).unloaded
            assert "error_message" in inspect(stored_submission).unloaded
            assert len(rendered.code) == judge.MAX_PROJECTED_CODE_CHARS
            assert rendered.code.endswith(judge.OUTPUT_TRUNCATION_MARKER)
            assert (
                len(rendered.error_message) == judge.MAX_PROJECTED_ERROR_MESSAGE_CHARS
            )
            assert rendered.error_message.endswith(judge.OUTPUT_TRUNCATION_MARKER)
            assert len(rendered.results) == judge.MAX_PROJECTED_RESULTS
            assert all(result.actual_output is None for result in rendered.results[:9])
            assert (
                len(rendered.results[9].actual_output)
                == judge.MAX_PROJECTED_OUTPUT_CHARS
            )
            assert "output truncated for response size" in rendered.results[9].message
            assert "message truncated" in rendered.results[9].message
            assert (
                rendered.results[9 + judge.MAX_PROJECTED_OUTPUT_ROWS].actual_output
                is None
            )
            assert "output truncated for response size" in rendered.results[-1].message
            assert "additional legacy results omitted" in rendered.results[-1].message
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_real_postgres_case_role_is_non_escalating_and_watchdogs_are_enforced(
    monkeypatch,
):
    """Optional defense-in-depth integration check against a local PostgreSQL."""
    try:
        with psycopg.connect(_admin_conninfo(), connect_timeout=2):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"local PostgreSQL unavailable: {exc}")

    token = uuid.uuid4().hex
    role_name = f"{judge.RUN_ROLE_PREFIX}{token}"
    db_name = f"{judge.RUN_DB_PREFIX}{token}"
    password = uuid.uuid4().hex
    role_created = False
    database_created = False
    runner = None
    try:
        try:
            _create_run_role(role_name, password)
            role_created = True
            _create_run_database(db_name, owner_role=role_name)
            database_created = True
        except (
            psycopg.errors.InsufficientPrivilege,
            psycopg.errors.UndefinedObject,
        ) as exc:
            pytest.skip(
                f"PostgreSQL judge roles are not configured for integration testing: {exc}"
            )

        try:
            runner = psycopg.connect(
                _runner_conninfo(db_name, role_name, password), autocommit=True
            )
        except psycopg.OperationalError as exc:
            pytest.skip(f"local pg_hba does not allow the transient judge login: {exc}")
        _disable_run_role_login(role_name)

        (
            session_user,
            current_user,
            can_login,
            is_super,
            can_create_role,
            can_create_db,
            inherits,
        ) = runner.execute(
            """
            SELECT session_user, current_user, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolinherit
            FROM pg_roles WHERE rolname = current_user
            """
        ).fetchone()
        assert session_user == current_user == role_name
        assert (can_login, is_super, can_create_role, can_create_db, inherits) == (
            False,
            False,
            False,
            False,
            False,
        )
        assert not runner.execute(
            "SELECT pg_has_role(current_user, %s, 'MEMBER')",
            (settings.judge_runner_role,),
        ).fetchone()[0]

        # A database owner can mutate the marker used by startup cleanup, so
        # the SQL guard must reject the command before it reaches PostgreSQL.
        database_comment_sql = (
            f"COMMENT ON DATABASE {db_name} IS 'tampered by submission'"
        )
        with pytest.raises(UnsafeSqlError, match="database metadata"):
            validate_user_sql(database_comment_sql)
        runner.execute(database_comment_sql)
        assert (
            runner.execute(
                "SELECT shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = current_database()"
            ).fetchone()[0]
            == "tampered by submission"
        )
        runner.execute(f"COMMENT ON DATABASE {db_name} IS '{judge.RUN_DB_COMMENT}'")

        runner.execute("RESET ROLE")
        assert runner.execute("SELECT session_user, current_user").fetchone() == (
            role_name,
            role_name,
        )

        unicode_bypass = (
            r"""SELECT U&"set\005fconfig"('statement_timeout', '123ms', false)"""
        )
        with pytest.raises(UnsafeSqlError):
            validate_user_sql(unicode_bypass)
        assert runner.execute(unicode_bypass).fetchone()[0] == "123ms"

        xml_bypass = """
            SELECT query_to_xml(
                $$SELECT set_config('statement_timeout', '0', false)$$,
                false,
                true,
                ''
            )
        """
        with pytest.raises(UnsafeSqlError):
            validate_user_sql(xml_bypass)
        runner.execute("SET statement_timeout = '999ms'")
        assert runner.execute(xml_bypass).fetchone()[0]
        assert runner.execute("SHOW statement_timeout").fetchone()[0] == "0"

        ts_stat_bypass = """
            SELECT * FROM ts_stat(
                $$SELECT to_tsvector(set_config('statement_timeout', '0', false))$$
            )
        """
        with pytest.raises(UnsafeSqlError):
            validate_user_sql(ts_stat_bypass)
        runner.execute("SET statement_timeout = '999ms'")
        assert runner.execute(ts_stat_bypass).fetchall()
        assert runner.execute("SHOW statement_timeout").fetchone()[0] == "0"

        logical_wal = "SELECT pg_logical_emit_message(false, 'oj_test', 'content')"
        with pytest.raises(UnsafeSqlError):
            validate_user_sql(logical_wal)
        assert runner.execute(logical_wal).fetchone()[0] is not None

        aggregate_bypass = """
            CREATE AGGREGATE evil_set(text, boolean) (
                SFUNC = pg_catalog.set_config,
                STYPE = text,
                INITCOND = 'statement_timeout'
            )
        """
        with pytest.raises(UnsafeSqlError):
            validate_user_sql(aggregate_bypass)
        runner.execute(aggregate_bypass)
        runner.execute("SET statement_timeout = '999ms'")
        assert runner.execute("SELECT evil_set('0', false)").fetchone()[0] == "0"
        assert runner.execute("SHOW statement_timeout").fetchone()[0] == "0"

        result_count, first_result = _execute_user_sql(
            runner,
            "CREATE TEMP TABLE streamed(x int); INSERT INTO streamed VALUES (1), (2) RETURNING x",
            collect_first=True,
        )
        assert result_count == 1
        assert first_result == "x\n1\n2\n"
        empty_count, empty_result = _execute_user_sql(
            runner,
            "SELECT x FROM streamed WHERE false",
            collect_first=True,
        )
        assert empty_count == 1
        assert empty_result == "x\n"
        empty_dml_count, empty_dml_result = _execute_user_sql(
            runner,
            "DELETE FROM streamed WHERE false RETURNING x",
            collect_first=True,
        )
        assert empty_dml_count == 1
        assert empty_dml_result == "x\n"
        mixed_count, mixed_first = _execute_user_sql(
            runner,
            "DELETE FROM streamed WHERE false RETURNING x; SELECT count(*) AS n FROM streamed",
            collect_first=True,
        )
        assert mixed_count == 2
        assert mixed_first == "x\n"
        transaction_count, transaction_first = _execute_user_sql(
            runner,
            "BEGIN; INSERT INTO streamed VALUES (3) RETURNING x; ROLLBACK; SELECT count(*) AS n FROM streamed",
            collect_first=True,
        )
        assert transaction_count == 2
        assert transaction_first == "x\n3\n"
        assert runner.execute("SELECT count(*) FROM streamed").fetchone()[0] == 2
        with pytest.raises(psycopg.errors.UndefinedTable):
            _execute_user_sql(
                runner, "SELECT * FROM definitely_missing", collect_first=True
            )

        runner.execute("SET statement_timeout = 0")
        with pytest.raises(SqlDeadlineExceeded):
            with _query_deadline(runner, 100, db_name, role_name):
                runner.execute("SELECT pg_sleep(0.075); SELECT pg_sleep(0.075)")

        monkeypatch.setattr(judge, "MAX_RUN_DATABASE_BYTES", 1)
        with pytest.raises(DatabaseSizeExceeded):
            with _query_deadline(runner, 2_000, db_name, role_name):
                runner.execute("SELECT pg_sleep(1)")
        runner.close()
        runner = None

        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(
                _runner_conninfo(db_name, role_name, password), connect_timeout=2
            )
    finally:
        if runner is not None:
            runner.close()
        if database_created:
            _drop_run_database(db_name)
        if role_created:
            _drop_run_role(role_name)

    with psycopg.connect(_admin_conninfo()) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)
            ).fetchone()
            is None
        )
