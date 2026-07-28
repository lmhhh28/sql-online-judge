import uuid

import psycopg
import pytest
from psycopg import sql

from app.config import psycopg_connection_url, settings
from app.scripts.setup_db import (
    SETUP_LOCK_ID,
    _acquire_setup_lock,
    _admin_conninfo_for_database,
    _application_database_name,
    _configure_application_database,
    _ensure_application_database,
    _ensure_restricted_runner_role,
    _release_setup_lock,
    _remove_runner_role_memberships,
)


class _Result:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _RecordingConnection:
    def __init__(self, *, database_exists: bool = True, runner_exists: bool = False):
        self.database_exists = database_exists
        self.runner_exists = runner_exists
        self.statements: list[tuple[str, object]] = []

    def execute(self, statement, parameters=None):
        rendered = (
            statement.as_string() if hasattr(statement, "as_string") else statement
        )
        self.statements.append((rendered, parameters))
        if "SELECT EXISTS" in rendered:
            return _Result([(False,)])
        if "WHERE member.rolname = %s" in rendered:
            return _Result([("legacy_parent", "parent_grantor")])
        if "WHERE granted.rolname = %s" in rendered:
            return _Result([("legacy_member", "member_grantor")])
        if "FROM pg_database" in rendered:
            return _Result([(1,)]) if self.database_exists else _Result()
        if "SELECT 1 FROM pg_roles" in rendered:
            return _Result([(1,)]) if self.runner_exists else _Result()
        return _Result()


def test_application_database_name_comes_from_settings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "app_database_url",
        "postgresql+psycopg://app:secret@localhost/custom_oj?sslmode=disable",
    )

    assert _application_database_name() == "custom_oj"


def test_application_database_name_requires_explicit_database(monkeypatch):
    monkeypatch.setattr(
        settings, "app_database_url", "postgresql+psycopg://app@localhost"
    )

    # libpq defaults the database to the user at connection time, but setup
    # must not guess which database it is authorized to create.
    with pytest.raises(RuntimeError, match="database name"):
        _application_database_name()


def test_application_database_name_uses_effective_dbname_override(monkeypatch):
    monkeypatch.setattr(
        settings,
        "app_database_url",
        "postgresql+psycopg:///path-name?dbname=effective_name",
    )

    assert _application_database_name() == "effective_name"


def test_setup_admin_connection_enforces_bounded_connection_timeout(monkeypatch):
    monkeypatch.setattr(
        settings,
        "admin_database_url",
        "postgresql+psycopg://admin@localhost/postgres?connect_timeout=9999",
    )

    parameters = psycopg.conninfo.conninfo_to_dict(
        _admin_conninfo_for_database("target_database")
    )

    assert parameters["dbname"] == "target_database"
    assert parameters["connect_timeout"] == "5"


def test_runner_memberships_are_revoked_in_both_directions_with_grantor(
    monkeypatch,
):
    monkeypatch.setattr(settings, "judge_runner_role", "oj_runner")
    connection = _RecordingConnection()

    _remove_runner_role_memberships(connection, "db_admin")

    rendered = [statement for statement, _ in connection.statements]
    assert (
        'REVOKE "legacy_parent" FROM "oj_runner" GRANTED BY "parent_grantor" CASCADE'
    ) in rendered
    assert (
        'REVOKE "oj_runner" FROM "legacy_member" GRANTED BY "member_grantor" CASCADE'
    ) in rendered


def test_new_application_database_is_owned_by_application_role():
    connection = _RecordingConnection(database_exists=False)

    _ensure_application_database(
        connection,
        database_name="custom_oj",
        application_user="app_user",
        current_user="db_admin",
    )

    rendered = [statement for statement, _ in connection.statements]
    assert 'CREATE DATABASE "custom_oj" OWNER "app_user" TEMPLATE template0' in rendered


def test_setup_uses_bounded_session_advisory_lock():
    connection = _RecordingConnection()

    _acquire_setup_lock(connection)
    _release_setup_lock(connection)

    assert connection.statements[0][0].startswith("SET lock_timeout TO")
    assert connection.statements[1][0].startswith("SET statement_timeout TO")
    assert connection.statements[2] == (
        "SELECT pg_advisory_lock(%s)",
        (SETUP_LOCK_ID,),
    )
    assert connection.statements[-1] == (
        "SELECT pg_advisory_unlock(%s)",
        (SETUP_LOCK_ID,),
    )


def test_existing_runner_password_and_role_settings_are_cleared(monkeypatch):
    monkeypatch.setattr(settings, "judge_runner_role", "oj_runner")
    connection = _RecordingConnection(runner_exists=True)

    _ensure_restricted_runner_role(connection)

    rendered = [statement for statement, _ in connection.statements]
    assert any(
        statement.startswith('ALTER ROLE "oj_runner" NOLOGIN PASSWORD NULL')
        for statement in rendered
    )
    assert 'ALTER ROLE "oj_runner" RESET ALL' in rendered


def test_distinct_application_role_owns_new_database_and_can_create_schema_objects():
    suffix = uuid.uuid4().hex[:16]
    database_name = f"oj_setup_{suffix}"
    application_user = f"oj_setup_app_{suffix}"
    admin_url = psycopg_connection_url(settings.admin_database_url)
    created_role = False
    created_database = False

    try:
        try:
            with psycopg.connect(admin_url, autocommit=True) as admin:
                current_user = admin.execute("SELECT current_user").fetchone()[0]
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(
                        sql.Identifier(application_user)
                    )
                )
                created_role = True
                _ensure_application_database(
                    admin,
                    database_name,
                    application_user,
                    current_user,
                )
                created_database = True
                _configure_application_database(admin, database_name, application_user)
        except (psycopg.OperationalError, psycopg.errors.InsufficientPrivilege) as exc:
            pytest.skip(f"local PostgreSQL cannot create setup test resources: {exc}")

        with psycopg.connect(admin_url) as admin:
            owner = admin.execute(
                """
                SELECT pg_get_userbyid(datdba)
                FROM pg_database
                WHERE datname = %s
                """,
                (database_name,),
            ).fetchone()[0]
        assert owner == application_user

        with psycopg.connect(
            _admin_conninfo_for_database(database_name), autocommit=True
        ) as database_connection:
            database_connection.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(application_user))
            )
            database_connection.execute(
                "CREATE TABLE public.setup_privilege_probe (id integer)"
            )
            database_connection.execute("DROP TABLE public.setup_privilege_probe")
    finally:
        try:
            with psycopg.connect(admin_url, autocommit=True) as admin:
                if created_database:
                    admin.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(database_name)
                        )
                    )
                if created_role:
                    admin.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(application_user)
                        )
                    )
        except psycopg.OperationalError:
            pass


def test_existing_database_keeps_owner_while_application_role_gets_migration_access():
    suffix = uuid.uuid4().hex[:16]
    database_name = f"oj_existing_{suffix}"
    application_user = f"oj_existing_app_{suffix}"
    admin_url = psycopg_connection_url(settings.admin_database_url)
    created_role = False
    created_database = False

    try:
        try:
            with psycopg.connect(admin_url, autocommit=True) as admin:
                original_owner = admin.execute("SELECT current_user").fetchone()[0]
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN").format(
                        sql.Identifier(application_user)
                    )
                )
                created_role = True
                admin.execute(
                    sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                        sql.Identifier(database_name)
                    )
                )
                created_database = True
                _configure_application_database(admin, database_name, application_user)
        except (psycopg.OperationalError, psycopg.errors.InsufficientPrivilege) as exc:
            pytest.skip(f"local PostgreSQL cannot create setup test resources: {exc}")

        with psycopg.connect(admin_url) as admin:
            owner = admin.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s",
                (database_name,),
            ).fetchone()[0]
        assert owner == original_owner

        with psycopg.connect(
            _admin_conninfo_for_database(database_name), autocommit=True
        ) as database_connection:
            database_connection.execute(
                sql.SQL("SET ROLE {}").format(sql.Identifier(application_user))
            )
            database_connection.execute(
                "CREATE TABLE public.existing_privilege_probe (id integer)"
            )
            database_connection.execute("DROP TABLE public.existing_privilege_probe")
    finally:
        try:
            with psycopg.connect(admin_url, autocommit=True) as admin:
                if created_database:
                    admin.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(database_name)
                        )
                    )
                if created_role:
                    admin.execute(
                        sql.SQL("DROP ROLE IF EXISTS {}").format(
                            sql.Identifier(application_user)
                        )
                    )
        except psycopg.OperationalError:
            pass
