from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from jwt.exceptions import InvalidTokenError

from app.config import settings

PASSWORD_ITERATIONS = 600_000
_MIN_PASSWORD_ITERATIONS = 100_000
_MAX_PASSWORD_ITERATIONS = 2_000_000
JWT_ISSUER = "sql-online-judge"
MAX_DATABASE_ID = 2_147_483_647
# This deliberately public hash is used only to equalize missing-user login work.
DUMMY_PASSWORD_HASH = (  # nosec B105
    "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$"
    "pEwEvIzAQ/HJgF1Rb3nCcubixiM4G3Y+GoIy8vl8VNQ="
)


def hash_password(password: str) -> str:
    if not isinstance(password, str):
        raise ValueError("Password must be text")
    try:
        password_bytes = password.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Password must contain valid UTF-8 characters") from exc
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (
        PASSWORD_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        password_bytes = password.encode("utf-8")
    except (AttributeError, UnicodeEncodeError):
        return False
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iteration_count = int(iterations)
        if not _MIN_PASSWORD_ITERATIONS <= iteration_count <= _MAX_PASSWORD_ITERATIONS:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        if len(salt) != 16 or len(expected) != hashlib.sha256().digest_size:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iteration_count)
        return hmac.compare_digest(actual, expected)
    except (AttributeError, ValueError, TypeError, binascii.Error):
        return False


def password_needs_rehash(encoded: str) -> bool:
    try:
        algorithm, iterations, _, _ = encoded.split("$", 3)
        return algorithm != "pbkdf2_sha256" or int(iterations) < PASSWORD_ITERATIONS
    except (ValueError, TypeError):
        return True


def create_access_token(subject: str) -> str:
    if (
        not isinstance(subject, str)
        or not subject.isascii()
        or not subject.isdecimal()
        or int(subject) <= 0
        or int(subject) > MAX_DATABASE_ID
        or str(int(subject)) != subject
    ):
        raise ValueError("Token subject must be a positive canonical integer string")
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_expires_minutes)
    payload = {
        "sub": subject,
        "iss": JWT_ISSUER,
        "iat": now,
        "nbf": now,
        "exp": expire,
    }
    return jwt.encode(
        payload, settings.jwt_signing_key, algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_signing_key,
            algorithms=[settings.jwt_algorithm],
            issuer=JWT_ISSUER,
            options={"require": ["sub", "iss", "iat", "nbf", "exp"]},
        )
        subject = payload.get("sub")
        if (
            not isinstance(subject, str)
            or not subject.isascii()
            or not subject.isdecimal()
        ):
            return None
        user_id = int(subject)
        return (
            user_id
            if 0 < user_id <= MAX_DATABASE_ID and str(user_id) == subject
            else None
        )
    except (InvalidTokenError, ValueError, TypeError):
        return None
