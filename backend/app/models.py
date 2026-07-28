from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    """Return naive UTC for the existing TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'user')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    submissions = relationship(
        "Submission", back_populates="user", passive_deletes=True
    )


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (
        CheckConstraint("checker IN ('result', 'string')", name="ck_problems_checker"),
        CheckConstraint(
            "time_limit_ms BETWEEN 100 AND 30000", name="ck_problems_time_limit_ms"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(
        String(80), unique=True, index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    statement_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    schema_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    difficulty: Mapped[str] = mapped_column(String(32), nullable=False, default="入门")
    tags: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    checker: Mapped[str] = mapped_column(String(24), nullable=False, default="result")
    ordered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    time_limit_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    test_cases = relationship(
        "TestCase",
        back_populates="problem",
        cascade="all, delete-orphan",
        order_by="TestCase.id",
    )
    submissions = relationship(
        "Submission", back_populates="problem", passive_deletes=True
    )


class TestCase(Base):
    __tablename__ = "test_cases"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1000000", name="ck_test_cases_score"),
        UniqueConstraint("problem_id", "name", name="uq_test_case_problem_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    setup_sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    judge_sql: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    problem = relationship("Problem", back_populates="test_cases")
    results = relationship(
        "TestResult", back_populates="test_case", passive_deletes=True
    )


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint("mode IN ('run', 'submit')", name="ck_submissions_mode"),
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'AC', 'WA', 'CE', 'RE', 'TLE', 'SE')",
            name="ck_submissions_status",
        ),
        CheckConstraint(
            "score >= 0 AND score <= max_score AND max_score <= 100000000",
            name="ck_submissions_scores",
        ),
        Index("ix_submissions_user_id_id", "user_id", "id"),
        Index("ix_submissions_user_id_status", "user_id", "status"),
        Index("ix_submissions_problem_id", "problem_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    problem_id: Mapped[int] = mapped_column(
        ForeignKey("problems.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    user = relationship("User", back_populates="submissions")
    problem = relationship("Problem", back_populates="submissions")
    results = relationship(
        "TestResult",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="TestResult.id",
    )


class TestResult(Base):
    __tablename__ = "test_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('AC', 'WA', 'CE', 'RE', 'TLE', 'SE')",
            name="ck_test_results_status",
        ),
        CheckConstraint(
            "score >= 0 AND score <= max_score AND max_score <= 1000000",
            name="ck_test_results_scores",
        ),
        CheckConstraint("runtime_ms >= 0", name="ck_test_results_runtime_ms"),
        Index("ix_test_results_submission_id_id", "submission_id", "id"),
        Index("ix_test_results_test_case_id", "test_case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    test_case_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    runtime_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now
    )

    submission = relationship("Submission", back_populates="results")
    test_case = relationship("TestCase", back_populates="results")
