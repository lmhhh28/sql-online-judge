from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import main as main_module
from app.database import Base, get_db
from app.main import MAX_REQUEST_BODY_SIZE, RequestBodyLimitMiddleware, app
from app.models import Problem, Submission, User
from app.models import TestCase as CaseModel
from app.models import TestResult as ResultModel
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import problems as problems_router
from app.schemas import CodeSubmitRequest
from app.security import create_access_token, hash_password


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def override_get_db():
        db = testing_session()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(admin_router, "SessionLocal", testing_session)
    monkeypatch.setattr(
        problems_router, "judge_submission", lambda _submission_id: None
    )
    client = TestClient(app, base_url="http://localhost")
    try:
        yield client, testing_session
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _register(
    client: TestClient, username: str, password: str = "correct horse battery staple"
) -> str:
    response = client.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _authorization(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _problem_package(slug: str = "raw-import") -> bytes:
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "problem.yaml",
            f"slug: {slug}\ntitle: Raw import\nchecker: result\ntime_limit_ms: 1000\n",
        )
        archive.writestr("statement.md", "# Imported")
        archive.writestr("tests/sample_1/judge.sql", "SELECT 1 AS value")
        archive.writestr("tests/sample_1/expected.csv", "value\n1\n")
    return data.getvalue()


def test_registration_login_and_bearer_contract(api):
    client, testing_session = api

    first = client.post(
        "/api/auth/register",
        json={"username": "  admin.one  ", "password": "correct horse battery staple"},
    )
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    first_token = first.json()["access_token"]
    authenticated_me = client.get("/api/me", headers=_authorization(first_token))
    assert authenticated_me.headers["cache-control"] == "no-store"
    assert authenticated_me.headers["x-content-type-options"] == "nosniff"
    assert authenticated_me.json()["role"] == "admin"
    assert authenticated_me.json()["username"] == "admin.one"
    assert authenticated_me.json()["created_at"].endswith("Z")

    normalized_login = client.post(
        "/api/auth/login",
        json={
            "username": "  admin.one  ",
            "password": "correct horse battery staple",
        },
    )
    assert normalized_login.status_code == 200

    with testing_session() as db:
        colliding_legacy_user = User(
            username="  admin.one  ",
            password_hash=hash_password("legacy collision password"),
            role="user",
        )
        legacy_user = User(
            username=" legacy @ user ",
            password_hash=hash_password("legacy password"),
            role="user",
        )
        db.add_all([colliding_legacy_user, legacy_user])
        db.commit()
    exact_login = client.post(
        "/api/auth/login",
        json={
            "username": "  admin.one  ",
            "password": "legacy collision password",
        },
    )
    assert exact_login.status_code == 200
    exact_me = client.get(
        "/api/me", headers=_authorization(exact_login.json()["access_token"])
    )
    assert exact_me.json()["username"] == "  admin.one  "
    assert (
        client.post(
            "/api/auth/login",
            json={
                "username": "  admin.one  ",
                "password": "correct horse battery staple",
            },
        ).status_code
        == 401
    )
    legacy_login = client.post(
        "/api/auth/login",
        json={"username": " legacy @ user ", "password": "legacy password"},
    )
    assert legacy_login.status_code == 200

    second_token = _register(client, "normal_user")
    assert (
        client.get("/api/me", headers=_authorization(second_token)).json()["role"]
        == "user"
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "normal_user", "password": "another safe password"},
        ).status_code
        == 409
    )
    role_injection = client.post(
        "/api/auth/register",
        json={
            "username": "role-injection",
            "password": "correct horse battery staple",
            "role": "admin",
        },
    )
    assert role_injection.status_code == 422

    weak = client.post(
        "/api/auth/register", json={"username": "short-pass", "password": "1234567"}
    )
    assert weak.status_code == 422
    assert "1234567" not in weak.text
    invalid_login = client.post(
        "/api/auth/login", json={"username": "missing-user", "password": "wrong"}
    )
    assert invalid_login.status_code == 401
    assert invalid_login.headers["www-authenticate"] == "Bearer"

    missing = client.get("/api/me")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    openapi = client.get("/openapi.json").json()
    bearer = openapi["components"]["securitySchemes"]["HTTPBearer"]
    assert bearer == {"type": "http", "scheme": "bearer"}
    import_body = openapi["paths"]["/api/admin/problems/import"]["post"]["requestBody"]
    assert import_body["content"]["application/zip"]["schema"]["format"] == "binary"
    assert openapi["paths"]["/api/problems"]["get"]["security"] == [
        {},
        {"HTTPBearer": []},
    ]


def test_request_models_reject_unencodable_or_nul_text_without_echoing_input(api):
    client, _ = api
    surrogate_password = b'{"username":"surrogate-user","password":"\\ud800\\ud800\\ud800\\ud800\\ud800\\ud800\\ud800\\ud800"}'
    register = client.post(
        "/api/auth/register",
        content=surrogate_password,
        headers={"Content-Type": "application/json"},
    )
    assert register.status_code == 422
    assert "ud800" not in register.text.lower()

    login = client.post(
        "/api/auth/login",
        content=b'{"username":"surrogate-user","password":"\\ud800"}',
        headers={"Content-Type": "application/json"},
    )
    assert login.status_code == 422
    assert "ud800" not in login.text.lower()

    token = _register(client, "valid-after-surrogate")
    submit = client.post(
        "/api/problems/missing/run",
        content=b'{"code":"\\ud800"}',
        headers={**_authorization(token), "Content-Type": "application/json"},
    )
    assert submit.status_code == 422
    assert "ud800" not in submit.text.lower()

    nul_problem = client.post(
        "/api/admin/problems",
        content=b'{"slug":"nul-problem","title":"bad\\u0000title"}',
        headers={**_authorization(token), "Content-Type": "application/json"},
    )
    assert nul_problem.status_code == 422
    assert "u0000" not in nul_problem.text.lower()

    nul_code = client.post(
        "/api/problems/missing/run",
        content=b'{"code":"SELECT \\u0000"}',
        headers={**_authorization(token), "Content-Type": "application/json"},
    )
    assert nul_code.status_code == 422
    assert "u0000" not in nul_code.text.lower()


def test_postgresql_registration_uses_a_transaction_scoped_lock():
    statements = []

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement, parameters):
            statements.append((str(statement), parameters))

    auth_router._prevent_registration_races(FakeSession())

    assert len(statements) == 1
    assert "pg_advisory_xact_lock" in statements[0][0]


def test_invalid_credentials_are_not_silently_anonymous(api):
    client, _ = api

    health = client.get("/api/health")
    assert health.json() == {"status": "ok"}
    assert health.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/problems").status_code == 200
    malformed = client.get(
        "/api/problems", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert malformed.status_code == 401
    wrong_scheme = client.get("/api/problems", headers={"Authorization": "Basic abc"})
    assert wrong_scheme.status_code == 401
    invalid_host = client.get("/api/health", headers={"Host": "attacker.example"})
    assert invalid_host.status_code == 400
    assert invalid_host.headers["x-content-type-options"] == "nosniff"


def test_host_and_request_limits_run_before_cors():
    middleware_order = [entry.cls for entry in app.user_middleware]

    assert middleware_order == [
        main_module.SecurityHeadersMiddleware,
        TrustedHostMiddleware,
        RequestBodyLimitMiddleware,
        CORSMiddleware,
    ]


def test_invalid_host_is_rejected_without_reading_unknown_length_body():
    sent_messages = []
    receive_called = False

    async def receive():
        nonlocal receive_called
        receive_called = True
        raise AssertionError("an invalid host body must not be read")

    async def send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/health",
        "raw_path": b"/api/health",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"attacker.example")],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
    }

    asyncio.run(app.build_middleware_stack()(scope, receive, send))

    assert receive_called is False
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 400
    assert Headers(raw=sent_messages[0]["headers"])["x-content-type-options"] == (
        "nosniff"
    )


def test_unknown_length_oversized_cors_preflight_is_limited_before_cors():
    sent_messages = []
    incoming = iter(
        [
            {
                "type": "http.request",
                "body": b"x" * (MAX_REQUEST_BODY_SIZE + 1),
                "more_body": False,
            }
        ]
    )

    async def receive():
        return next(incoming)

    async def send(message):
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "OPTIONS",
        "scheme": "http",
        "path": "/api/problems",
        "raw_path": b"/api/problems",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"localhost"),
            (b"origin", b"http://localhost:5173"),
            (b"access-control-request-method", b"GET"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 80),
    }

    asyncio.run(app.build_middleware_stack()(scope, receive, send))

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 413
    headers = Headers(raw=sent_messages[0]["headers"])
    assert headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in headers


def test_health_reports_database_outages(api):
    client, _ = api

    class BrokenSession:
        def execute(self, _statement):
            raise SQLAlchemyError("database is down")

    def broken_database():
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_database
    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable"}


def test_global_request_size_limit_rejects_oversized_declared_bodies(api):
    client, _ = api
    response = client.post(
        "/api/auth/register",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(MAX_REQUEST_BODY_SIZE + 1),
            "Origin": "http://localhost:5173",
        },
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large"}
    assert "access-control-allow-origin" not in response.headers


def test_global_request_size_limit_rejects_extremely_long_content_length():
    sent_messages = []
    downstream_called = False

    async def receive():
        raise AssertionError("an oversized declared body must not be read")

    async def send(message):
        sent_messages.append(message)

    async def downstream(_scope, _receive_body, _send):
        nonlocal downstream_called
        downstream_called = True

    middleware = RequestBodyLimitMiddleware(downstream, max_body_size=4)
    asyncio.run(
        middleware(
            {"type": "http", "headers": [(b"content-length", b"9" * 5000)]},
            receive,
            send,
        )
    )

    assert downstream_called is False
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 413


def test_global_request_size_limit_counts_streamed_chunks():
    sent_messages = []
    incoming = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def send(message):
        sent_messages.append(message)

    async def consume_body(_scope, receive_body, _send):
        while True:
            message = await receive_body()
            if not message.get("more_body"):
                return

    middleware = RequestBodyLimitMiddleware(consume_body, max_body_size=4)
    asyncio.run(middleware({"type": "http", "headers": []}, receive, send))

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 413


def test_request_size_overflow_never_emits_a_second_response_start():
    sent_messages = []
    incoming = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def send(message):
        sent_messages.append(message)

    async def starts_then_reads(_scope, receive_body, send_response):
        await send_response(
            {"type": "http.response.start", "status": 200, "headers": []}
        )
        await receive_body()
        await receive_body()

    middleware = RequestBodyLimitMiddleware(starts_then_reads, max_body_size=4)
    with pytest.raises(main_module._RequestBodyTooLarge):
        asyncio.run(
            middleware(
                {"type": "http", "headers": [(b"content-length", b"4")]},
                receive,
                send,
            )
        )

    assert [message["type"] for message in sent_messages] == ["http.response.start"]


def test_global_request_size_limit_drains_unknown_length_body_before_routing():
    sent_messages = []
    downstream_called = False
    incoming = iter(
        [
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ]
    )

    async def receive():
        return next(incoming)

    async def send(message):
        sent_messages.append(message)

    async def ignore_body(_scope, _receive_body, _send):
        nonlocal downstream_called
        downstream_called = True

    middleware = RequestBodyLimitMiddleware(ignore_body, max_body_size=4)
    asyncio.run(middleware({"type": "http", "headers": []}, receive, send))

    assert downstream_called is False
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 413


def test_unknown_length_body_replays_many_small_chunks_as_one_message():
    chunk_count = 20_000
    chunks_read = 0
    replayed_messages = []

    async def receive():
        nonlocal chunks_read
        chunks_read += 1
        return {
            "type": "http.request",
            "body": b"x",
            "more_body": chunks_read < chunk_count,
        }

    async def send(_message):
        return None

    async def consume_body(_scope, receive_body, _send):
        replayed_messages.append(await receive_body())

    middleware = RequestBodyLimitMiddleware(consume_body, max_body_size=chunk_count)
    asyncio.run(middleware({"type": "http", "headers": []}, receive, send))

    assert chunks_read == chunk_count
    assert replayed_messages == [
        {
            "type": "http.request",
            "body": b"x" * chunk_count,
            "more_body": False,
        }
    ]


def test_unknown_length_body_preserves_disconnect_after_partial_request():
    incoming = iter(
        [
            {"type": "http.request", "body": b"partial", "more_body": True},
            {"type": "http.disconnect"},
        ]
    )
    replayed_messages = []

    async def receive():
        return next(incoming)

    async def send(_message):
        return None

    async def consume_body(_scope, receive_body, _send):
        replayed_messages.append(await receive_body())
        replayed_messages.append(await receive_body())
        replayed_messages.append(await receive_body())

    middleware = RequestBodyLimitMiddleware(consume_body, max_body_size=100)
    asyncio.run(middleware({"type": "http", "headers": []}, receive, send))

    assert replayed_messages == [
        {"type": "http.request", "body": b"partial", "more_body": True},
        {"type": "http.disconnect"},
        {"type": "http.disconnect"},
    ]


def test_lifespan_always_stops_judge_executor_if_startup_is_cancelled(monkeypatch):
    calls = []

    def verify_schema():
        calls.append("verify-schema")

    def start():
        calls.append("start")

    def recover():
        raise AssertionError("recovery is dispatched by the fake threadpool")

    def shutdown(wait):
        calls.append(("shutdown", wait))

    async def fake_run_in_threadpool(function, *args):
        if function is verify_schema:
            return function(*args)
        if function is recover:
            calls.append("recover")
            raise asyncio.CancelledError
        calls.append("shutdown")
        return function(*args)

    monkeypatch.setattr(main_module, "verify_database_schema_current", verify_schema)
    monkeypatch.setattr(main_module, "start_judge_executor", start)
    monkeypatch.setattr(main_module, "recover_interrupted_judges", recover)
    monkeypatch.setattr(main_module, "shutdown_judge_executor", shutdown)
    monkeypatch.setattr(main_module, "run_in_threadpool", fake_run_in_threadpool)

    async def cancelled_startup():
        async with main_module.lifespan(main_module.app):
            raise AssertionError("cancelled startup must not reach the application")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled_startup())

    assert calls == [
        "verify-schema",
        "start",
        "recover",
        "shutdown",
        ("shutdown", True),
    ]


def test_lifespan_rejects_stale_schema_before_starting_executor(monkeypatch):
    executor_started = False

    def reject_schema():
        raise RuntimeError("Run `alembic upgrade head`")

    def start():
        nonlocal executor_started
        executor_started = True

    async def run_direct(function, *args):
        return function(*args)

    monkeypatch.setattr(main_module, "verify_database_schema_current", reject_schema)
    monkeypatch.setattr(main_module, "start_judge_executor", start)
    monkeypatch.setattr(main_module, "run_in_threadpool", run_direct)

    async def start_application():
        async with main_module.lifespan(main_module.app):
            raise AssertionError("stale schema must not reach the application")

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        asyncio.run(start_application())
    assert executor_started is False


def test_problem_validation_duplicate_case_and_active_submission_guard(
    api, monkeypatch
):
    client, _ = api
    admin_token = _register(client, "admin-user")
    headers = _authorization(admin_token)

    assert (
        client.post(
            "/api/admin/problems",
            headers=headers,
            json={"slug": "Upper_Case", "title": "Bad"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/problems",
            headers=headers,
            json={"slug": "valid-problem", "title": "Bad", "checker": "unknown"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/admin/problems",
            headers=headers,
            json={"slug": "valid-problem", "title": "Bad", "tags": ["sql", "sql"]},
        ).status_code
        == 422
    )

    created = client.post(
        "/api/admin/problems",
        headers=headers,
        json={"slug": "valid-problem", "title": " Valid problem ", "checker": "result"},
    )
    assert created.status_code == 200, created.text
    assert created.json()["is_active"] is False
    problem_id = created.json()["id"]
    assert created.json()["title"] == "Valid problem"
    assert (
        client.post(
            "/api/admin/problems",
            headers=headers,
            json={"slug": "valid-problem", "title": "Duplicate"},
        ).status_code
        == 409
    )

    assert (
        client.put(
            f"/api/admin/problems/{problem_id}", headers=headers, json={}
        ).status_code
        == 422
    )
    assert (
        client.put(
            f"/api/admin/problems/{problem_id}",
            headers=headers,
            json={"title": None},
        ).status_code
        == 422
    )

    case = {
        "name": "sample_1",
        "is_hidden": False,
        "score": 1,
        "judge_sql": "SELECT 1 AS value",
        "expected_output": "value\n1\n",
    }
    added = client.post(
        f"/api/admin/problems/{problem_id}/test-cases", headers=headers, json=case
    )
    assert added.status_code == 200, added.text
    assert (
        client.post(
            f"/api/admin/problems/{problem_id}/test-cases",
            headers=headers,
            json=case,
        ).status_code
        == 409
    )
    monkeypatch.setattr(admin_router, "MAX_TEST_CASES", 1)
    too_many = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json={**case, "name": "sample_2"},
    )
    assert too_many.status_code == 409
    assert too_many.json()["detail"] == "Problem may contain at most 1 test cases"
    invalid_score = {**case, "name": "sample_2", "score": "NaN"}
    assert (
        client.post(
            f"/api/admin/problems/{problem_id}/test-cases",
            headers=headers,
            json=invalid_score,
        ).status_code
        == 422
    )

    activated = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"is_active": True},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["created_at"].endswith("Z")

    submitted = client.post(
        "/api/problems/valid-problem/submit",
        headers=headers,
        json={"code": "SELECT 1"},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "PENDING"
    assert submitted.json()["created_at"].endswith("Z")
    blocked_update = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"title": "Changed during judge"},
    )
    assert blocked_update.status_code == 409
    blocked_case = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json={**case, "name": "sample_2"},
    )
    assert blocked_case.status_code == 409


def test_legacy_ascii_slugs_remain_readable_and_submittable(api, monkeypatch):
    client, testing_session = api
    with testing_session() as db:
        problem = Problem(slug="Legacy_Name", title="Legacy problem")
        db.add(problem)
        db.flush()
        db.add(
            CaseModel(
                problem_id=problem.id,
                name="sample_1",
                is_hidden=False,
                score=1,
                setup_sql="",
                judge_sql="SELECT 1 AS value",
                expected_output="value\n1\n",
            )
        )
        db.commit()

    original_problem_item = problems_router._problem_to_list_item

    def bounded_problem_item(problem, accepted=False):
        assert {"statement_md", "schema_md"} <= inspect(problem).unloaded
        return original_problem_item(problem, accepted)

    with monkeypatch.context() as patch:
        patch.setattr(problems_router, "_problem_to_list_item", bounded_problem_item)
        listed = client.get("/api/problems")
    assert listed.status_code == 200
    assert listed.json()[0]["slug"] == "Legacy_Name"
    assert client.get("/api/problems/Legacy_Name").status_code == 200

    headers = _authorization(_register(client, "legacy-runner"))
    submitted = client.post(
        "/api/problems/Legacy_Name/run",
        headers=headers,
        json={"code": "SELECT 1"},
    )
    assert submitted.status_code == 200, submitted.text


def test_problem_list_has_bounded_stable_pagination(api):
    client, testing_session = api
    with testing_session() as db:
        db.add_all(
            [
                Problem(slug="page-one", title="Page one"),
                Problem(slug="page-two", title="Page two"),
                Problem(
                    slug="page-three",
                    title="Page three",
                    tags=["window-functions", "100%_safe"],
                ),
                Problem(slug="literal-percent", title="100% SQL"),
                Problem(
                    slug="tag-wildcard-decoy",
                    title="Wildcard decoy",
                    tags=["100XXsafe"],
                ),
            ]
        )
        db.commit()

    page = client.get("/api/problems?limit=1&offset=1")

    assert page.status_code == 200
    assert [problem["slug"] for problem in page.json()] == ["page-two"]
    assert page.headers["x-total-count"] == "5"
    assert "Authorization" in page.headers["vary"]
    searched = client.get(
        "/api/problems?search=TWO",
        headers={"Origin": "http://localhost:5173"},
    )
    assert [problem["slug"] for problem in searched.json()] == ["page-two"]
    assert searched.headers["x-total-count"] == "1"
    assert "X-Total-Count" in searched.headers["access-control-expose-headers"]
    literal_wildcard = client.get("/api/problems?search=%25")
    assert [problem["slug"] for problem in literal_wildcard.json()] == [
        "page-three",
        "literal-percent",
    ]
    tag_search = client.get(
        "/api/problems?search=WINDOW-FUNCTIONS",
        headers={"Origin": "http://localhost:5173"},
    )
    assert [problem["slug"] for problem in tag_search.json()] == ["page-three"]
    assert tag_search.headers["x-total-count"] == "1"
    assert "X-Total-Count" in tag_search.headers["access-control-expose-headers"]
    literal_tag_wildcards = client.get("/api/problems?search=100%25_safe")
    assert [problem["slug"] for problem in literal_tag_wildcards.json()] == [
        "page-three"
    ]
    assert client.get("/api/problems?search=%5B").json() == []
    assert client.get("/api/problems?limit=501").status_code == 422
    assert client.get("/api/problems?offset=-1").status_code == 422
    assert client.get("/api/problems?offset=1000001").status_code == 422
    assert client.get("/api/problems?search=%00").status_code == 422


def test_manual_problem_activation_enforces_complete_byte_bounded_cases(
    api, monkeypatch
):
    client, _ = api
    headers = _authorization(_register(client, "activation-admin"))

    premature = client.post(
        "/api/admin/problems",
        headers=headers,
        json={"slug": "premature-active", "title": "Premature", "is_active": True},
    )
    assert premature.status_code == 422

    created = client.post(
        "/api/admin/problems",
        headers=headers,
        json={"slug": "activation-check", "title": "Activation check"},
    )
    assert created.status_code == 200, created.text
    problem_id = created.json()["id"]
    assert created.json()["is_active"] is False

    with monkeypatch.context() as patch:
        patch.setattr(admin_router, "MAX_MEMBER_SIZE", 8)
        oversized_setup = client.post(
            f"/api/admin/problems/{problem_id}/test-cases",
            headers=headers,
            json={
                "name": "too_wide",
                "setup_sql": "界" * 3,
                "judge_sql": "SELECT 1",
                "expected_output": "value\n1\n",
            },
        )
    assert oversized_setup.status_code == 422
    assert "setup_sql" in oversized_setup.json()["detail"]

    added = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json={
            "name": "sample_1",
            "is_hidden": False,
            "judge_sql": "SELECT 1",
            "expected_output": "value\n1\n",
        },
    )
    assert added.status_code == 200, added.text

    monkeypatch.setattr(admin_router, "MAX_PROBLEM_TEXT_LENGTH", 8)
    stored_while_inactive = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"statement_md": "界" * 3},
    )
    assert stored_while_inactive.status_code == 200
    rejected_activation = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"is_active": True},
    )
    assert rejected_activation.status_code == 422
    assert "statement_md" in rejected_activation.json()["detail"]

    activated = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"statement_md": "Ready", "is_active": True},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["is_active"] is True


def test_submission_authorization_and_hidden_result_redaction(api, monkeypatch):
    client, testing_session = api
    with testing_session() as db:
        owner = User(
            username="owner", password_hash=hash_password("owner password"), role="user"
        )
        stranger = User(
            username="stranger",
            password_hash=hash_password("stranger password"),
            role="user",
        )
        admin = User(
            username="admin",
            password_hash=hash_password("admin password"),
            role="admin",
        )
        problem = Problem(slug="hidden-data", title="Hidden data")
        db.add_all([owner, stranger, admin, problem])
        db.flush()
        public_case = CaseModel(
            problem_id=problem.id,
            name="sample_1",
            is_hidden=False,
            score=1,
            setup_sql="PUBLIC SETUP",
            judge_sql="PUBLIC JUDGE",
            expected_output="public expected",
        )
        hidden_case = CaseModel(
            problem_id=problem.id,
            name="hidden_1",
            is_hidden=True,
            score=1,
            setup_sql="SECRET INPUT",
            judge_sql="SECRET JUDGE",
            expected_output="SECRET EXPECTED",
        )
        db.add_all([public_case, hidden_case])
        db.flush()
        submission = Submission(
            user_id=owner.id,
            problem_id=problem.id,
            code="SELECT secret",
            mode="submit",
            status="WA",
            score=1,
            max_score=2,
            error_message="Some test cases failed",
        )
        db.add(submission)
        db.flush()
        db.add_all(
            [
                ResultModel(
                    submission_id=submission.id,
                    test_case_id=public_case.id,
                    name=public_case.name,
                    status="AC",
                    score=1,
                    max_score=1,
                    runtime_ms=1,
                    message="Accepted",
                    actual_output="public actual",
                    expected_output="public expected",
                    is_hidden=False,
                ),
                ResultModel(
                    submission_id=submission.id,
                    test_case_id=hidden_case.id,
                    name=hidden_case.name,
                    status="WA",
                    score=0,
                    max_score=1,
                    runtime_ms=2,
                    message="SECRET COMPARISON DETAIL",
                    actual_output="SECRET ACTUAL",
                    expected_output="SECRET EXPECTED",
                    is_hidden=True,
                ),
            ]
        )
        db.commit()
        owner_id, stranger_id, admin_id, submission_id = (
            owner.id,
            stranger.id,
            admin.id,
            submission.id,
        )

    owner_headers = _authorization(create_access_token(str(owner_id)))
    stranger_headers = _authorization(create_access_token(str(stranger_id)))
    admin_headers = _authorization(create_access_token(str(admin_id)))

    original_problem_detail = problems_router.problem_to_detail

    def bounded_problem_detail(problem, accepted=False):
        assert [case.name for case in problem.test_cases] == ["sample_1"]
        assert {"setup_sql", "judge_sql"} <= inspect(problem.test_cases[0]).unloaded
        return original_problem_detail(problem, accepted)

    with monkeypatch.context() as patch:
        patch.setattr(problems_router, "problem_to_detail", bounded_problem_detail)
    detail = client.get("/api/problems/hidden-data")
    assert detail.status_code == 200
    assert "Authorization" in detail.headers["vary"]
    rendered_problem = detail.text
    assert "SECRET INPUT" not in rendered_problem
    assert "SECRET JUDGE" not in rendered_problem
    assert "SECRET EXPECTED" not in rendered_problem
    assert [case["name"] for case in detail.json()["public_cases"]] == ["sample_1"]

    forbidden = client.get(
        f"/api/submissions/{submission_id}", headers=stranger_headers
    )
    assert forbidden.status_code == 404
    assert (
        client.get("/api/submissions/2147483648", headers=owner_headers).status_code
        == 422
    )

    owner_view = client.get(f"/api/submissions/{submission_id}", headers=owner_headers)
    assert owner_view.status_code == 200
    hidden_result = next(
        result for result in owner_view.json()["results"] if result["is_hidden"]
    )
    assert hidden_result["actual_output"] is None
    assert hidden_result["expected_output"] is None
    assert hidden_result["message"] == "Hidden test case"
    assert "SECRET ACTUAL" not in owner_view.text
    assert "SECRET EXPECTED" not in owner_view.text

    admin_view = client.get(f"/api/submissions/{submission_id}", headers=admin_headers)
    assert admin_view.status_code == 200
    admin_hidden_result = next(
        result for result in admin_view.json()["results"] if result["is_hidden"]
    )
    assert admin_hidden_result["actual_output"] == "SECRET ACTUAL"
    assert admin_hidden_result["expected_output"] == "SECRET EXPECTED"

    original_list_item = problems_router.submission_to_list_item

    def bounded_list_item(item):
        assert {"code", "error_message"} <= inspect(item).unloaded
        assert {"statement_md", "schema_md"} <= inspect(item.problem).unloaded
        return original_list_item(item)

    monkeypatch.setattr(problems_router, "submission_to_list_item", bounded_list_item)
    monkeypatch.setattr(admin_router, "submission_to_list_item", bounded_list_item)

    owner_history = client.get("/api/submissions", headers=owner_headers)
    assert owner_history.status_code == 200
    assert owner_history.json()[0]["id"] == submission_id
    assert "code" not in owner_history.json()[0]
    assert "results" not in owner_history.json()[0]
    assert "error_message" not in owner_history.json()[0]

    assert (
        client.get("/api/admin/submissions", headers=owner_headers).status_code == 403
    )
    admin_history = client.get("/api/admin/submissions", headers=admin_headers)
    assert admin_history.status_code == 200
    assert "code" not in admin_history.json()[0]
    assert "results" not in admin_history.json()[0]
    assert "error_message" not in admin_history.json()[0]
    own_list = client.get("/api/submissions?mine=false", headers=stranger_headers)
    assert own_list.status_code == 200
    assert own_list.json() == []


def test_case_output_matches_checker_and_checker_changes_revalidate_cases(
    api, monkeypatch
):
    client, testing_session = api
    headers = _authorization(_register(client, "checker-admin"))
    created = client.post(
        "/api/admin/problems",
        headers=headers,
        json={"slug": "checker-validation", "title": "Checker validation"},
    )
    problem_id = created.json()["id"]
    malformed_csv_case = {
        "name": "sample_bad",
        "is_hidden": False,
        "judge_sql": "SELECT 1",
        "expected_output": "first,second\nonly-one\n",
    }

    rejected = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json=malformed_csv_case,
    )
    assert rejected.status_code == 422
    assert "row 2" in rejected.json()["detail"]

    with monkeypatch.context() as patch:
        patch.setattr(admin_router, "MAX_UNCOMPRESSED_SIZE", 10)
        content_limit = client.post(
            f"/api/admin/problems/{problem_id}/test-cases",
            headers=headers,
            json={
                "name": "sample_size",
                "judge_sql": "SELECT 1",
                "expected_output": "value\n1\n",
            },
        )
    assert content_limit.status_code == 422
    assert "per-problem limit" in content_limit.json()["detail"]

    changed_to_string = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"checker": "string"},
    )
    assert changed_to_string.status_code == 200
    oversized_bytes = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json={
            **malformed_csv_case,
            "name": "too_large",
            "expected_output": "界" * 400_000,
        },
    )
    assert oversized_bytes.status_code == 422
    assert "1000000-byte limit" in oversized_bytes.json()["detail"]
    valid_case = {
        **malformed_csv_case,
        "name": "sample_valid",
        "expected_output": "value\nfree form\n",
    }
    accepted = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json=valid_case,
    )
    assert accepted.status_code == 200

    changed_to_result = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"checker": "result"},
    )
    assert changed_to_result.status_code == 200
    activated = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"is_active": True},
    )
    assert activated.status_code == 200, activated.text

    with testing_session() as db:
        db.add(
            CaseModel(
                problem_id=problem_id,
                name="legacy_bad",
                is_hidden=True,
                score=1,
                setup_sql="",
                judge_sql="SELECT 1",
                expected_output="first,second\nonly-one\n",
            )
        )
        db.commit()

    invalid_change = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"checker": "string"},
    )
    assert invalid_change.status_code == 422
    assert client.get("/api/problems/checker-validation").json()["checker"] == "result"


def test_active_submission_limit_prevents_per_user_judge_flooding(api):
    client, _ = api
    headers = _authorization(_register(client, "submission-limit-admin"))
    created = client.post(
        "/api/admin/problems",
        headers=headers,
        json={"slug": "submission-limit", "title": "Submission limit"},
    )
    problem_id = created.json()["id"]
    added = client.post(
        f"/api/admin/problems/{problem_id}/test-cases",
        headers=headers,
        json={
            "name": "sample_1",
            "is_hidden": False,
            "judge_sql": "SELECT 1 AS value",
            "expected_output": "value\n1\n",
        },
    )
    assert added.status_code == 200
    activated = client.put(
        f"/api/admin/problems/{problem_id}",
        headers=headers,
        json={"is_active": True},
    )
    assert activated.status_code == 200, activated.text

    for _ in range(problems_router.MAX_ACTIVE_SUBMISSIONS_PER_USER):
        accepted = client.post(
            "/api/problems/submission-limit/run",
            headers=headers,
            json={"code": "SELECT 1"},
        )
        assert accepted.status_code == 200

    rejected = client.post(
        "/api/problems/submission-limit/run",
        headers=headers,
        json={"code": "SELECT 1"},
    )
    assert rejected.status_code == 429
    assert rejected.headers["retry-after"] == "1"


def test_submission_is_enqueued_even_if_response_construction_fails(api, monkeypatch):
    _, testing_session = api
    queued: list[int] = []

    with testing_session() as db:
        user = User(
            username="enqueue-owner",
            password_hash=hash_password("correct horse battery staple"),
            role="user",
        )
        problem = Problem(slug="enqueue-before-response", title="Enqueue first")
        db.add_all([user, problem])
        db.flush()
        db.add(
            CaseModel(
                problem_id=problem.id,
                name="sample_1",
                is_hidden=False,
                score=1,
                setup_sql="",
                judge_sql="SELECT 1 AS value",
                expected_output="value\n1\n",
            )
        )
        db.commit()

        monkeypatch.setattr(problems_router, "judge_submission", queued.append)

        def fail_response_construction(*_args, **_kwargs):
            raise RuntimeError("response construction failed")

        monkeypatch.setattr(
            problems_router, "submission_to_read", fail_response_construction
        )
        with pytest.raises(RuntimeError, match="response construction failed"):
            problems_router._create_submission(
                problem.slug,
                CodeSubmitRequest(code="SELECT 1"),
                "run",
                db,
                user,
            )

        submission_id = db.scalar(select(Submission.id))

    assert queued == [submission_id]


def test_raw_zip_import_is_streamed_and_multipart_is_rejected(api, monkeypatch):
    client, _ = api
    admin_headers = _authorization(_register(client, "zip-admin"))
    package = _problem_package()

    wrong_type = client.post(
        "/api/admin/problems/import",
        headers={**admin_headers, "Content-Type": "multipart/form-data"},
        content=package,
    )
    assert wrong_type.status_code == 415

    original_problem_item = admin_router._problem_to_list_item

    def bounded_problem_item(problem, accepted=False):
        assert "test_cases" in inspect(problem).unloaded
        return original_problem_item(problem, accepted)

    with monkeypatch.context() as patch:
        patch.setattr(admin_router, "_problem_to_list_item", bounded_problem_item)
        imported = client.post(
            "/api/admin/problems/import",
            headers={**admin_headers, "Content-Type": "application/zip"},
            content=package,
        )
    assert imported.status_code == 200, imported.text
    assert imported.json()["imported_cases"] == 1
    assert imported.json()["problem"]["slug"] == "raw-import"
    assert "public_cases" not in imported.json()["problem"]
    assert "statement_md" not in imported.json()["problem"]
    assert "schema_md" not in imported.json()["problem"]

    monkeypatch.setattr(admin_router, "MAX_ARCHIVE_SIZE", 8)
    oversized = client.post(
        "/api/admin/problems/import",
        headers={**admin_headers, "Content-Type": "application/zip"},
        content=b"123456789",
    )
    assert oversized.status_code == 413


def test_raw_zip_reader_rejects_extremely_long_content_length_before_streaming():
    class DeclaredOversizeRequest:
        headers = Headers(
            {
                "content-type": "application/zip",
                "content-length": "9" * 5000,
            }
        )

        async def stream(self):
            raise AssertionError("an oversized declared archive must not be read")
            yield b""  # pragma: no cover

    with pytest.raises(HTTPException) as error:
        asyncio.run(admin_router._read_zip_request(DeclaredOversizeRequest()))

    assert error.value.status_code == 413
