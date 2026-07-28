import getpass
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from psycopg.conninfo import conninfo_to_dict
from pydantic import Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_INSECURE_JWT_SECRETS = {"", "change-this-dev-secret"}
_EPHEMERAL_JWT_SECRET = secrets.token_urlsafe(48)
_RESERVED_RUN_DATABASE_PREFIX = "oj_run_"
_RESERVED_RUN_ROLE_PREFIX = "oj_exec_"
_FORBIDDEN_LIBPQ_ENVIRONMENT = ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS")
DATABASE_CONNECT_TIMEOUT_SECONDS = 5


def psycopg_connection_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def database_connection_parameters(url: str) -> dict[str, str]:
    try:
        return conninfo_to_dict(psycopg_connection_url(url))
    except Exception as exc:
        raise ValueError("Database URL contains invalid connection parameters") from exc


def effective_database_user(url: str) -> str:
    return (
        database_connection_parameters(url).get("user")
        or os.environ.get("PGUSER")
        or getpass.getuser()
    )


class Settings(BaseSettings):
    app_database_url: str = "postgresql+psycopg:///sql_oj?host=/tmp"
    admin_database_url: str = "postgresql+psycopg:///postgres?host=/tmp"
    judge_runner_role: str = "oj_runner"
    jwt_secret_key: str = ""
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expires_minutes: int = Field(default=10080, gt=0, le=43200)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    trusted_hosts: str = "localhost,127.0.0.1"

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        hide_input_in_errors=True,
    )

    @field_validator("app_database_url", "admin_database_url")
    @classmethod
    def validate_database_url(cls, value: str, info: ValidationInfo) -> str:
        try:
            url = make_url(value)
        except Exception as exc:
            raise ValueError("Database URL is invalid") from exc
        if url.drivername != "postgresql+psycopg":
            raise ValueError("Database URLs must use the postgresql+psycopg driver")
        for parameter in ("dbname", "user"):
            if isinstance(url.query.get(parameter), tuple):
                raise ValueError(
                    f"Database URLs must not contain multiple {parameter} parameters"
                )
        if any(
            parameter in url.query
            for parameter in ("service", "servicefile", "options")
        ):
            raise ValueError(
                "Database URLs must not use service, servicefile, or startup options"
            )
        parameters = database_connection_parameters(value)
        database_name = parameters.get("dbname")
        if not database_name:
            raise ValueError("Database URLs must include a database name")
        if database_name.startswith(_RESERVED_RUN_DATABASE_PREFIX):
            raise ValueError(
                f"Database names beginning with {_RESERVED_RUN_DATABASE_PREFIX!r} "
                "are reserved for disposable judge databases"
            )
        if info.field_name == "app_database_url" and database_name in {
            "postgres",
            "template0",
            "template1",
        }:
            raise ValueError(
                "APP_DATABASE_URL must not use a PostgreSQL maintenance or template database"
            )
        return value

    @field_validator("judge_runner_role")
    @classmethod
    def validate_judge_runner_role(cls, value: str) -> str:
        if len(value) > 63 or re.fullmatch(r"[a-z_][a-z0-9_]*", value) is None:
            raise ValueError(
                "JUDGE_RUNNER_ROLE must be a lowercase PostgreSQL identifier of at most 63 characters"
            )
        if value == "postgres" or value.startswith("pg_"):
            raise ValueError("JUDGE_RUNNER_ROLE must be a dedicated non-reserved role")
        if value.startswith(_RESERVED_RUN_ROLE_PREFIX):
            raise ValueError(
                f"JUDGE_RUNNER_ROLE must not use the reserved {_RESERVED_RUN_ROLE_PREFIX!r} prefix"
            )
        return value

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if value not in _INSECURE_JWT_SECRETS and len(value.encode("utf-8")) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 bytes")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: str) -> str:
        for origin in (item.strip() for item in value.split(",")):
            if not origin:
                continue
            parsed = urlsplit(origin)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError(f"Invalid CORS origin: {origin}") from exc
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or "*" in parsed.hostname
            ):
                raise ValueError(f"Invalid CORS origin: {origin}")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError(
                    f"CORS origins must not contain paths, queries, or fragments: {origin}"
                )
        return value

    @field_validator("trusted_hosts")
    @classmethod
    def validate_trusted_hosts(cls, value: str) -> str:
        hosts = [host.strip() for host in value.split(",") if host.strip()]
        if not hosts:
            raise ValueError("TRUSTED_HOSTS must contain at least one host")
        for host in hosts:
            if (
                host == "*"
                or "://" in host
                or ":" in host
                or "/" in host
                or any(character.isspace() for character in host)
            ):
                raise ValueError(f"Invalid trusted host: {host}")
            if "*" in host and (not host.startswith("*.") or "*" in host[1:]):
                raise ValueError(f"Invalid trusted host wildcard: {host}")
        return value

    @model_validator(mode="after")
    def validate_runner_is_dedicated(self) -> "Settings":
        unsafe_environment = [
            variable
            for variable in _FORBIDDEN_LIBPQ_ENVIRONMENT
            if os.environ.get(variable)
        ]
        if unsafe_environment:
            raise ValueError(
                "Unsafe libpq environment variables are set: "
                + ", ".join(unsafe_environment)
                + ". Put explicit connection settings in the database URLs instead"
            )
        connection_users = {
            effective_database_user(self.app_database_url),
            effective_database_user(self.admin_database_url),
        }
        if any(user.startswith(_RESERVED_RUN_ROLE_PREFIX) for user in connection_users):
            raise ValueError(
                "Application and administrator database users must not use the "
                f"reserved {_RESERVED_RUN_ROLE_PREFIX!r} prefix"
            )
        if self.judge_runner_role in connection_users:
            raise ValueError(
                "JUDGE_RUNNER_ROLE must differ from application and administrator database users"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return list(
            dict.fromkeys(
                origin.strip().rstrip("/")
                for origin in self.cors_origins.split(",")
                if origin.strip()
            )
        )

    @property
    def trusted_host_list(self) -> list[str]:
        return list(
            dict.fromkeys(
                host.strip() for host in self.trusted_hosts.split(",") if host.strip()
            )
        )

    @property
    def jwt_signing_key(self) -> str:
        if self.jwt_secret_key in _INSECURE_JWT_SECRETS:
            return _EPHEMERAL_JWT_SECRET
        return self.jwt_secret_key

    @property
    def jwt_uses_ephemeral_secret(self) -> bool:
        return self.jwt_secret_key in _INSECURE_JWT_SECRETS


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
