from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class UserRead(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    username: str
    password: str


class ProblemBase(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    title: str = Field(min_length=1, max_length=200)
    statement_md: str = ""
    schema_md: str = ""
    difficulty: str = "入门"
    tags: list[str] = []
    checker: str = "result"
    ordered: bool = False
    time_limit_ms: int = Field(default=3000, ge=100, le=30000)
    is_active: bool = True


class ProblemCreate(ProblemBase):
    pass


class ProblemUpdate(BaseModel):
    title: Optional[str] = None
    statement_md: Optional[str] = None
    schema_md: Optional[str] = None
    difficulty: Optional[str] = None
    tags: Optional[list[str]] = None
    checker: Optional[str] = None
    ordered: Optional[bool] = None
    time_limit_ms: Optional[int] = Field(default=None, ge=100, le=30000)
    is_active: Optional[bool] = None


class PublicTestCaseRead(BaseModel):
    id: int
    name: str
    score: float
    is_hidden: bool
    expected_output: Optional[str] = None

    model_config = {"from_attributes": True}


class TestCaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    is_hidden: bool = True
    score: float = Field(default=1.0, ge=0)
    setup_sql: str = ""
    judge_sql: str
    expected_output: str = ""


class ProblemListItem(BaseModel):
    id: int
    slug: str
    title: str
    difficulty: str
    tags: list[str]
    checker: str
    ordered: bool
    time_limit_ms: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ProblemDetail(ProblemListItem):
    statement_md: str
    schema_md: str
    public_cases: list[PublicTestCaseRead]


class CodeSubmitRequest(BaseModel):
    code: str = Field(min_length=1, max_length=50000)


class TestResultRead(BaseModel):
    id: int
    name: str
    status: str
    score: float
    max_score: float
    runtime_ms: int
    message: Optional[str]
    actual_output: Optional[str]
    expected_output: Optional[str]
    is_hidden: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SubmissionRead(BaseModel):
    id: int
    user_id: int
    problem_id: int
    problem_slug: str
    problem_title: str
    username: str
    code: str
    mode: str
    status: str
    score: float
    max_score: float
    error_message: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    results: list[TestResultRead]


class ImportResponse(BaseModel):
    problem: ProblemDetail
    imported_cases: int


class ApiMessage(BaseModel):
    message: str
    detail: Optional[Any] = None
