import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from app.config import (
    DATABASE_CONNECT_TIMEOUT_SECONDS,
    database_connection_parameters,
    effective_database_user,
    settings,
)

SETUP_LOCK_ID = 0x53514C4F4A534554
SETUP_LOCK_TIMEOUT_MS = 10_000
SETUP_STATEMENT_TIMEOUT_MS = 30_000


def _application_database_name() -> str:
    database_name = database_connection_parameters(settings.app_database_url).get(
        "dbname"
    )
    if not database_name:
        raise RuntimeError("APP_DATABASE_URL must include a database name")
    return database_name


def _admin_conninfo_for_database(database_name: str) -> str:
    parameters = database_connection_parameters(settings.admin_database_url)
    parameters["dbname"] = database_name
    parameters["connect_timeout"] = str(DATABASE_CONNECT_TIMEOUT_SECONDS)
    return make_conninfo(**parameters)


def _configure_application_database(
    connection: psycopg.Connection, database_name: str, application_user: str
) -> None:
    try:
        can_connect = connection.execute(
            "SELECT has_database_privilege(%s, %s, 'CONNECT')",
            (application_user, database_name),
        ).fetchone()
        if can_connect is None or not can_connect[0]:
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(application_user),
                )
            )

        with psycopg.connect(
            _admin_conninfo_for_database(database_name), autocommit=True
        ) as database_connection:
            database_connection.execute(
                f"SET statement_timeout TO '{SETUP_STATEMENT_TIMEOUT_MS}ms'"
            )
            public_schema_exists = database_connection.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = 'public'"
            ).fetchone()
            if public_schema_exists is None:
                raise RuntimeError(
                    f"Database {database_name!r} has no public schema; refusing to guess a migration schema"
                )
            privileges = database_connection.execute(
                """
                SELECT has_database_privilege(%s, %s, 'CONNECT'),
                       has_schema_privilege(%s, 'public', 'USAGE'),
                       has_schema_privilege(%s, 'public', 'CREATE')
                """,
                (
                    application_user,
                    database_name,
                    application_user,
                    application_user,
                ),
            ).fetchone()
            if privileges is None:
                raise RuntimeError("Could not inspect application database privileges")
            if not privileges[1] or not privileges[2]:
                database_connection.execute(
                    sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                        sql.Identifier(application_user)
                    )
                )
                privileges = database_connection.execute(
                    """
                    SELECT has_database_privilege(%s, %s, 'CONNECT'),
                           has_schema_privilege(%s, 'public', 'USAGE'),
                           has_schema_privilege(%s, 'public', 'CREATE')
                    """,
                    (
                        application_user,
                        database_name,
                        application_user,
                        application_user,
                    ),
                ).fetchone()
    except psycopg.Error as exc:
        raise RuntimeError(
            f"Could not grant migration access to application role {application_user!r} "
            f"on database {database_name!r}"
        ) from exc
    if privileges is None or not all(privileges):
        raise RuntimeError(
            f"Application role {application_user!r} lacks CONNECT/USAGE/CREATE "
            f"privileges required to migrate database {database_name!r}"
        )


def _verify_application_connection(database_name: str, application_user: str) -> None:
    application_parameters = database_connection_parameters(settings.app_database_url)
    application_parameters["connect_timeout"] = str(DATABASE_CONNECT_TIMEOUT_SECONDS)
    try:
        with psycopg.connect(make_conninfo(**application_parameters)) as conn:
            conn.execute(f"SET statement_timeout TO '{SETUP_STATEMENT_TIMEOUT_MS}ms'")
            actual_database, actual_user, can_create = conn.execute(
                """
                SELECT current_database(), current_user,
                       has_schema_privilege(current_user, 'public', 'CREATE')
                """
            ).fetchone()
    except psycopg.Error as exc:
        raise RuntimeError(
            "APP_DATABASE_URL could not connect after database setup"
        ) from exc
    if actual_database != database_name or actual_user != application_user:
        raise RuntimeError(
            "APP_DATABASE_URL resolves to a different database or role than configured"
        )
    if not can_create:
        raise RuntimeError(
            f"Application role {application_user!r} cannot create migration objects in public"
        )


def _remove_runner_role_memberships(
    connection: psycopg.Connection, current_user: str
) -> None:
    inherited_roles = connection.execute(
        """
        SELECT granted.rolname, grantor.rolname
        FROM pg_auth_members membership
        JOIN pg_roles granted ON granted.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles grantor ON grantor.oid = membership.grantor
        WHERE member.rolname = %s
        """,
        (settings.judge_runner_role,),
    ).fetchall()
    for inherited_role, grantor in inherited_roles:
        connection.execute(
            sql.SQL("REVOKE {} FROM {} GRANTED BY {} CASCADE").format(
                sql.Identifier(inherited_role),
                sql.Identifier(settings.judge_runner_role),
                sql.Identifier(grantor),
            )
        )

    runner_members = connection.execute(
        """
        SELECT member.rolname, grantor.rolname
        FROM pg_auth_members membership
        JOIN pg_roles granted ON granted.oid = membership.roleid
        JOIN pg_roles member ON member.oid = membership.member
        JOIN pg_roles grantor ON grantor.oid = membership.grantor
        WHERE granted.rolname = %s
          AND member.rolname <> %s
        """,
        (settings.judge_runner_role, current_user),
    ).fetchall()
    for member, grantor in runner_members:
        connection.execute(
            sql.SQL("REVOKE {} FROM {} GRANTED BY {} CASCADE").format(
                sql.Identifier(settings.judge_runner_role),
                sql.Identifier(member),
                sql.Identifier(grantor),
            )
        )

    remaining_membership = connection.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE member.rolname = %s
        ) OR EXISTS (
            SELECT 1
            FROM pg_auth_members membership
            JOIN pg_roles granted ON granted.oid = membership.roleid
            JOIN pg_roles member ON member.oid = membership.member
            WHERE granted.rolname = %s
              AND member.rolname <> %s
        )
        """,
        (
            settings.judge_runner_role,
            settings.judge_runner_role,
            current_user,
        ),
    ).fetchone()
    if remaining_membership is None or remaining_membership[0]:
        raise RuntimeError("Could not remove all unsafe JUDGE_RUNNER_ROLE memberships")


def _ensure_application_database(
    connection: psycopg.Connection,
    database_name: str,
    application_user: str,
    current_user: str,
) -> None:
    db_exists = connection.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s", (database_name,)
    ).fetchone()
    if db_exists is not None:
        return
    try:
        connection.execute(
            sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0").format(
                sql.Identifier(database_name),
                sql.Identifier(application_user),
            )
        )
    except psycopg.errors.InsufficientPrivilege as exc:
        raise RuntimeError(
            f"Administrator role {current_user!r} cannot create database "
            f"{database_name!r} owned by {application_user!r}"
        ) from exc


def _ensure_restricted_runner_role(connection: psycopg.Connection) -> None:
    role_exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s",
        (settings.judge_runner_role,),
    ).fetchone()
    if role_exists is None:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} NOLOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(settings.judge_runner_role))
        )
    else:
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} NOLOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(settings.judge_runner_role))
        )
    connection.execute(
        sql.SQL("ALTER ROLE {} RESET ALL").format(
            sql.Identifier(settings.judge_runner_role)
        )
    )


def _acquire_setup_lock(connection: psycopg.Connection) -> None:
    try:
        connection.execute(f"SET lock_timeout TO '{SETUP_LOCK_TIMEOUT_MS}ms'")
        connection.execute(f"SET statement_timeout TO '{SETUP_STATEMENT_TIMEOUT_MS}ms'")
        connection.execute("SELECT pg_advisory_lock(%s)", (SETUP_LOCK_ID,))
    except psycopg.Error as exc:
        raise RuntimeError(
            "Could not acquire the database setup lock within 10 seconds"
        ) from exc


def _release_setup_lock(connection: psycopg.Connection) -> None:
    try:
        connection.execute("SELECT pg_advisory_unlock(%s)", (SETUP_LOCK_ID,))
    except psycopg.Error:
        # Closing the session releases a session advisory lock even if the
        # explicit unlock cannot be sent over a failed connection.
        pass


def _initialize_cluster(
    connection: psycopg.Connection,
    database_name: str,
    application_user: str,
) -> None:
    current_user = connection.execute("SELECT current_user").fetchone()[0]
    if (
        settings.judge_runner_role == current_user
        or settings.judge_runner_role == "postgres"
    ):
        raise RuntimeError("JUDGE_RUNNER_ROLE must be a dedicated restricted role")
    if settings.judge_runner_role.startswith("pg_"):
        raise RuntimeError(
            "JUDGE_RUNNER_ROLE must not use PostgreSQL's reserved pg_ prefix"
        )

    application_role = connection.execute(
        "SELECT rolcanlogin FROM pg_roles WHERE rolname = %s",
        (application_user,),
    ).fetchone()
    if application_role is None:
        raise RuntimeError(
            f"Application database role {application_user!r} does not exist"
        )
    if not application_role[0]:
        raise RuntimeError(
            f"Application database role {application_user!r} cannot log in"
        )

    _ensure_restricted_runner_role(connection)
    _remove_runner_role_memberships(connection, current_user)
    connection.execute(
        sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
            sql.Identifier(settings.judge_runner_role),
            sql.Identifier(current_user),
        )
    )
    _ensure_application_database(
        connection, database_name, application_user, current_user
    )
    _configure_application_database(connection, database_name, application_user)


def main() -> None:
    admin_parameters = database_connection_parameters(settings.admin_database_url)
    admin_parameters["connect_timeout"] = str(DATABASE_CONNECT_TIMEOUT_SECONDS)
    admin_url = make_conninfo(**admin_parameters)
    database_name = _application_database_name()
    application_user = effective_database_user(settings.app_database_url)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        _acquire_setup_lock(connection)
        try:
            _initialize_cluster(connection, database_name, application_user)
            _verify_application_connection(database_name, application_user)
        finally:
            _release_setup_lock(connection)

    print(
        f"Database setup complete: {database_name} and {settings.judge_runner_role} are ready."
    )


if __name__ == "__main__":
    main()
