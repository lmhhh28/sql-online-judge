from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_CONNECT_TIMEOUT_SECONDS, settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.app_database_url,
    pool_pre_ping=True,
    connect_args={"connect_timeout": DATABASE_CONNECT_TIMEOUT_SECONDS},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _expected_schema_heads() -> set[str]:
    backend_root = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_root / "alembic"))
    heads = set(ScriptDirectory.from_config(alembic_config).get_heads())
    if not heads:
        raise RuntimeError("No Alembic head revision is configured")
    return heads


def verify_database_schema_current(database_engine=engine) -> None:
    """Fail startup when PostgreSQL is not migrated to every repository head."""
    if database_engine.dialect.name != "postgresql":
        return
    expected_heads = _expected_schema_heads()
    try:
        with database_engine.connect() as connection:
            current_heads = set(
                connection.execute(text("SELECT version_num FROM alembic_version"))
                .scalars()
                .all()
            )
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Could not verify the PostgreSQL schema revision. Run `alembic upgrade head` "
            "after confirming APP_DATABASE_URL."
        ) from exc
    if current_heads != expected_heads:
        current = ", ".join(sorted(current_heads)) or "uninitialized"
        expected = ", ".join(sorted(expected_heads))
        raise RuntimeError(
            f"PostgreSQL schema is at {current}; expected {expected}. "
            "Run `alembic upgrade head` before starting the API."
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
