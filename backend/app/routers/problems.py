from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import get_current_user
from app.models import Problem, Submission, TestCase, User
from app.schemas import CodeSubmitRequest, ProblemDetail, ProblemListItem, PublicTestCaseRead, SubmissionRead
from app.services.judge import judge_submission, submission_to_read

router = APIRouter(prefix="/api", tags=["practice"])


def problem_to_detail(problem: Problem) -> ProblemDetail:
    public_cases = [
        PublicTestCaseRead(
            id=case.id,
            name=case.name,
            score=case.score,
            is_hidden=case.is_hidden,
            expected_output=case.expected_output if not case.is_hidden else None,
        )
        for case in problem.test_cases
        if not case.is_hidden
    ]
    return ProblemDetail(
        id=problem.id,
        slug=problem.slug,
        title=problem.title,
        statement_md=problem.statement_md,
        schema_md=problem.schema_md,
        difficulty=problem.difficulty,
        tags=problem.tags,
        checker=problem.checker,
        ordered=problem.ordered,
        time_limit_ms=problem.time_limit_ms,
        is_active=problem.is_active,
        created_at=problem.created_at,
        public_cases=public_cases,
    )


@router.get("/problems", response_model=list[ProblemListItem])
def list_problems(db: Session = Depends(get_db)) -> list[Problem]:
    return list(db.scalars(select(Problem).where(Problem.is_active.is_(True)).order_by(Problem.id)))


@router.get("/problems/{slug}", response_model=ProblemDetail)
def get_problem(slug: str, db: Session = Depends(get_db)) -> ProblemDetail:
    problem = db.scalar(
        select(Problem).options(selectinload(Problem.test_cases)).where(Problem.slug == slug, Problem.is_active.is_(True))
    )
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")
    return problem_to_detail(problem)


def _create_submission(
    slug: str,
    payload: CodeSubmitRequest,
    mode: str,
    background_tasks: BackgroundTasks,
    db: Session,
    current_user: User,
) -> SubmissionRead:
    problem = db.scalar(select(Problem).where(Problem.slug == slug, Problem.is_active.is_(True)))
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")

    selected_cases = select(TestCase).where(TestCase.problem_id == problem.id)
    if mode == "run":
        selected_cases = selected_cases.where(TestCase.is_hidden.is_(False))
    if (db.scalar(select(TestCase.id).where(TestCase.problem_id == problem.id).limit(1))) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Problem has no test cases")

    submission = Submission(user_id=current_user.id, problem_id=problem.id, code=payload.code, mode=mode)
    db.add(submission)
    db.commit()
    db.refresh(submission)
    background_tasks.add_task(judge_submission, submission.id)
    return submission_to_read(db, submission, viewer_is_admin=current_user.role == "admin")


@router.post("/problems/{slug}/run", response_model=SubmissionRead)
def run_problem(
    slug: str,
    payload: CodeSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubmissionRead:
    return _create_submission(slug, payload, "run", background_tasks, db, current_user)


@router.post("/problems/{slug}/submit", response_model=SubmissionRead)
def submit_problem(
    slug: str,
    payload: CodeSubmitRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubmissionRead:
    return _create_submission(slug, payload, "submit", background_tasks, db, current_user)


@router.get("/submissions/{submission_id}", response_model=SubmissionRead)
def get_submission(
    submission_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubmissionRead:
    submission = db.scalar(
        select(Submission)
        .options(selectinload(Submission.results), selectinload(Submission.problem), selectinload(Submission.user))
        .where(Submission.id == submission_id)
    )
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found")
    if submission.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    return submission_to_read(db, submission, viewer_is_admin=current_user.role == "admin")


@router.get("/submissions", response_model=list[SubmissionRead])
def list_submissions(
    mine: bool = Query(default=True),
    problem_slug: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SubmissionRead]:
    query = select(Submission).options(
        selectinload(Submission.results), selectinload(Submission.problem), selectinload(Submission.user)
    )
    if mine or current_user.role != "admin":
        query = query.where(Submission.user_id == current_user.id)
    if problem_slug:
        query = query.join(Problem).where(Problem.slug == problem_slug)
    query = query.order_by(Submission.id.desc()).limit(50)
    return [submission_to_read(db, item, viewer_is_admin=current_user.role == "admin") for item in db.scalars(query)]
