import psycopg
from psycopg import sql

from app.config import settings
from app.services.judge import _psycopg_url


def main() -> None:
    admin_url = _psycopg_url(settings.admin_database_url)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        role_exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (settings.judge_runner_role,)).fetchone()
        if role_exists is None:
            conn.execute(
                sql.SQL("CREATE ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT").format(
                    sql.Identifier(settings.judge_runner_role)
                )
            )
        current_user = conn.execute("SELECT current_user").fetchone()[0]
        if current_user != settings.judge_runner_role:
            conn.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(settings.judge_runner_role),
                    sql.Identifier(current_user),
                )
            )

        db_exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = 'sql_oj'").fetchone()
        if db_exists is None:
            conn.execute("CREATE DATABASE sql_oj TEMPLATE template0")

    print("Database setup complete: sql_oj and oj_runner are ready.")


if __name__ == "__main__":
    main()
