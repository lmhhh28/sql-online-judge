from __future__ import annotations

import base64
import getpass
import hashlib
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from pydantic import ValidationError

from app.config import Settings, settings
from app.security import (
    JWT_ISSUER,
    MAX_DATABASE_ID,
    PASSWORD_ITERATIONS,
    create_access_token,
    decode_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)


def _claims(subject="1", **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "iss": JWT_ISSUER,
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides)
    return claims


def _legacy_password_hash(password: str) -> str:
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return "pbkdf2_sha256$200000$%s$%s" % (
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def test_password_hashes_are_salted_strong_and_backward_compatible():
    password = "correct horse battery staple 密码"
    first = hash_password(password)
    second = hash_password(password)

    assert first != second
    assert first.split("$", 2)[1] == str(PASSWORD_ITERATIONS)
    assert verify_password(password, first)
    assert not verify_password("wrong", first)
    assert not password_needs_rehash(first)

    legacy = _legacy_password_hash(password)
    assert verify_password(password, legacy)
    assert password_needs_rehash(legacy)


def test_password_helpers_fail_safely_for_unencodable_text():
    with pytest.raises(ValueError, match="valid UTF-8"):
        hash_password("\ud800")
    assert not verify_password("\ud800", hash_password("valid password"))


@pytest.mark.parametrize(
    "encoded",
    [
        "not-a-password-hash",
        "pbkdf2_sha256$999999999$AAAAAAAAAAAAAAAAAAAAAA==$AAAA",
        "pbkdf2_sha256$600000$not-base64$not-base64",
        "other$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    ],
)
def test_malformed_password_hashes_fail_closed(encoded):
    assert not verify_password("password", encoded)


def test_jwt_requires_canonical_subject_issuer_expiry_and_algorithm():
    token = create_access_token("42")
    assert decode_access_token(token) == 42

    for subject in ("0", "01", "-1", "not-a-user", str(MAX_DATABASE_ID + 1)):
        with pytest.raises(ValueError):
            create_access_token(subject)

    wrong_subject = jwt.encode(
        _claims("not-a-user"), settings.jwt_signing_key, algorithm="HS256"
    )
    wrong_issuer = jwt.encode(
        _claims(iss="another-app"),
        settings.jwt_signing_key,
        algorithm="HS256",
    )
    expired = jwt.encode(
        _claims(exp=datetime.now(timezone.utc) - timedelta(seconds=1)),
        settings.jwt_signing_key,
        algorithm="HS256",
    )
    wrong_algorithm = jwt.encode(_claims(), settings.jwt_signing_key, algorithm="HS384")
    missing_claim = jwt.encode(
        {"sub": "1", "iss": JWT_ISSUER}, settings.jwt_signing_key, algorithm="HS256"
    )
    oversized_subject = jwt.encode(
        _claims(str(MAX_DATABASE_ID + 1)),
        settings.jwt_signing_key,
        algorithm="HS256",
    )

    assert decode_access_token(wrong_subject) is None
    assert decode_access_token(wrong_issuer) is None
    assert decode_access_token(expired) is None
    assert decode_access_token(wrong_algorithm) is None
    assert decode_access_token(missing_claim) is None
    assert decode_access_token(oversized_subject) is None
    assert decode_access_token("not-a-token") is None


def test_development_placeholder_is_not_the_effective_signing_key(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "change-this-dev-secret")
    with pytest.warns(jwt.InsecureKeyLengthWarning):
        forged = jwt.encode(_claims(), "change-this-dev-secret", algorithm="HS256")

    assert settings.jwt_uses_ephemeral_secret
    assert settings.jwt_signing_key != settings.jwt_secret_key
    assert decode_access_token(forged) is None
    assert decode_access_token(create_access_token("1")) == 1


def test_settings_reject_weak_explicit_secrets_bad_algorithms_and_unsafe_cors():
    with pytest.raises(ValidationError) as weak_secret_error:
        Settings(_env_file=None, jwt_secret_key="short")
    assert "input_value" not in str(weak_secret_error.value)
    assert "'short'" not in str(weak_secret_error.value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, jwt_algorithm="none")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins="*")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins="https://example.com/path")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins="https://user:secret@example.com")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins="https://*.example.com")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, cors_origins="https://example.com:not-a-port")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, trusted_hosts="*")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_database_url="sqlite:///unsafe.db")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, judge_runner_role="pg_superuser")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, judge_runner_role="Invalid-Role")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, judge_runner_role="oj_exec_reserved")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, judge_runner_role=getpass.getuser())
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg:///oj_run_not-disposable?host=/tmp",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            admin_database_url="postgresql+psycopg://oj_exec_reserved@/postgres?host=/tmp",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg:///sql_oj?user=one&user=two",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg:///sql_oj?dbname=one&dbname=two",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg:///safe?dbname=oj_run_override",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg:///safe?service=unsafe",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url="postgresql+psycopg:///safe?servicefile=/tmp/unsafe",
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url=(
                "postgresql+psycopg:///safe?options=-c%20role%3Doj_runner"
            ),
        )
    for maintenance_database in ("postgres", "template0", "template1"):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                app_database_url=(
                    "postgresql+psycopg:///safe?dbname=" + maintenance_database
                ),
            )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            app_database_url=("postgresql+psycopg:///sql_oj?host=/tmp&user=oj_runner"),
        )

    configured = Settings(
        _env_file=None,
        jwt_secret_key="a" * 32,
        cors_origins="https://example.com/,https://example.com",
        admin_database_url="postgresql+psycopg:///postgres?host=/tmp",
    )
    assert configured.jwt_signing_key == "a" * 32
    assert not configured.jwt_uses_ephemeral_secret
    assert configured.cors_origin_list == ["https://example.com"]


@pytest.mark.parametrize("variable", ["PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"])
def test_settings_reject_libpq_environment_that_can_override_connection(
    monkeypatch, variable
):
    for candidate in ("PGSERVICE", "PGSERVICEFILE", "PGOPTIONS"):
        monkeypatch.delenv(candidate, raising=False)
    monkeypatch.setenv(variable, "attacker-controlled")

    with pytest.raises(ValidationError, match=variable):
        Settings(_env_file=None)
