from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    WrapSerializer,
    field_validator,
    model_validator,
)

_USERNAME_PATTERN = r"^[\w.-]+$"


def normalize_username(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFKC", value.strip())
    return value


def canonical_username(value: str) -> Optional[str]:
    normalized = normalize_username(value)
    if (
        not 3 <= len(normalized) <= 64
        or re.fullmatch(_USERNAME_PATTERN, normalized) is None
    ):
        return None
    return normalized


Username = Annotated[
    str,
    BeforeValidator(normalize_username),
    StringConstraints(min_length=3, max_length=64, pattern=_USERNAME_PATTERN),
]
ProblemSlug = Annotated[
    str,
    StringConstraints(
        min_length=2, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    ),
]
ReadableProblemSlug = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$"),
]
ProblemTitle = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Difficulty = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)
]
Tag = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]
Checker = Literal["result", "string"]
SubmissionMode = Literal["run", "submit"]
SubmissionStatus = Literal["PENDING", "RUNNING", "AC", "WA", "CE", "RE", "TLE", "SE"]
TestResultStatus = Literal["AC", "WA", "CE", "RE", "TLE", "SE"]


def _serialize_utc_datetime(value: datetime, handler):
    normalized = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return handler(normalized)


UtcDateTime = Annotated[
    datetime,
    WrapSerializer(_serialize_utc_datetime, when_used="json"),
]


def _validate_unique_tags(tags: list[str]) -> list[str]:
    if len(set(tags)) != len(tags):
        raise ValueError("tags must not contain duplicates")
    return tags


def _validate_utf8_strings(value: Any) -> Any:
    pending = [value]
    visited_containers: set[int] = set()
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            if "\x00" in item:
                raise ValueError("Text fields must not contain NUL characters")
            try:
                item.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "Text fields must contain valid UTF-8 characters"
                ) from exc
            continue
        if isinstance(item, dict):
            identity = id(item)
            if identity in visited_containers:
                continue
            visited_containers.add(identity)
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            identity = id(item)
            if identity in visited_containers:
                continue
            visited_containers.add(identity)
            pending.extend(item)
    return value


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def validate_utf8_strings(cls, value: Any) -> Any:
        return _validate_utf8_strings(value)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(RequestModel):
    username: Username
    password: str = Field(min_length=8, max_length=128)


class UserRead(BaseModel):
    id: int
    username: str
    role: Literal["admin", "user"]
    created_at: UtcDateTime

    model_config = {"from_attributes": True}


class LoginRequest(RequestModel):
    # Registration canonicalizes new usernames, but login must continue to
    # accept exact V1 usernames that predate those restrictions.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ProblemBase(RequestModel):
    slug: ProblemSlug
    title: ProblemTitle
    statement_md: str = Field(default="", max_length=1_000_000)
    schema_md: str = Field(default="", max_length=1_000_000)
    difficulty: Difficulty = "入门"
    tags: list[Tag] = Field(default_factory=list, max_length=32)
    checker: Checker = "result"
    ordered: bool = False
    time_limit_ms: int = Field(default=3000, ge=100, le=30000)
    is_active: bool = True

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        return _validate_unique_tags(value)


class ProblemCreate(ProblemBase):
    # A manually-created problem has no test cases yet and must be explicitly
    # activated after cases are added and validated.
    is_active: bool = False


class ProblemUpdate(RequestModel):
    title: Optional[ProblemTitle] = None
    statement_md: Optional[str] = Field(default=None, max_length=1_000_000)
    schema_md: Optional[str] = Field(default=None, max_length=1_000_000)
    difficulty: Optional[Difficulty] = None
    tags: Optional[list[Tag]] = Field(default=None, max_length=32)
    checker: Optional[Checker] = None
    ordered: Optional[bool] = None
    time_limit_ms: Optional[int] = Field(default=None, ge=100, le=30000)
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def validate_update(self) -> ProblemUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        null_fields = [
            field for field in self.model_fields_set if getattr(self, field) is None
        ]
        if null_fields:
            raise ValueError(
                f"Fields may not be null: {', '.join(sorted(null_fields))}"
            )
        if "tags" in self.model_fields_set:
            _validate_unique_tags(self.tags or [])
        return self


class PublicTestCaseRead(BaseModel):
    id: int
    name: str
    score: float
    is_hidden: bool
    expected_output: Optional[str] = None

    model_config = {"from_attributes": True}


class TestCaseCreate(RequestModel):
    name: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )
    is_hidden: bool = True
    score: float = Field(default=1.0, ge=0, le=1_000_000, allow_inf_nan=False)
    setup_sql: str = Field(default="", max_length=2_000_000)
    judge_sql: str = Field(min_length=1, max_length=2_000_000, pattern=r"(?s).*\S.*")
    expected_output: str = Field(default="", max_length=1_000_000)


class ProblemListItem(BaseModel):
    id: int
    slug: ReadableProblemSlug
    title: str
    difficulty: str
    tags: list[str]
    checker: Checker
    ordered: bool
    time_limit_ms: int
    is_active: bool
    created_at: UtcDateTime
    accepted: bool = False

    model_config = {"from_attributes": True}


class ProblemDetail(ProblemListItem):
    statement_md: str
    schema_md: str
    public_cases: list[PublicTestCaseRead]


class CodeSubmitRequest(RequestModel):
    code: str = Field(min_length=1, max_length=50000, pattern=r"(?s).*\S.*")


class TestResultRead(BaseModel):
    id: int
    name: str
    status: TestResultStatus
    score: float
    max_score: float
    runtime_ms: int
    message: Optional[str]
    actual_output: Optional[str]
    expected_output: Optional[str]
    is_hidden: bool
    created_at: UtcDateTime

    model_config = {"from_attributes": True}


class SubmissionRead(BaseModel):
    id: int
    user_id: int
    problem_id: int
    problem_slug: str
    problem_title: str
    username: str
    code: str
    mode: SubmissionMode
    status: SubmissionStatus
    score: float
    max_score: float
    error_message: Optional[str]
    created_at: UtcDateTime
    completed_at: Optional[UtcDateTime]
    results: list[TestResultRead]


class SubmissionListItem(BaseModel):
    id: int
    user_id: int
    problem_id: int
    problem_slug: str
    problem_title: str
    username: str
    mode: SubmissionMode
    status: SubmissionStatus
    score: float
    max_score: float
    created_at: UtcDateTime
    completed_at: Optional[UtcDateTime]


class ImportResponse(BaseModel):
    problem: ProblemListItem
    imported_cases: int


class ApiMessage(BaseModel):
    message: str
    detail: Optional[Any] = None
