from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Query,
    Response,
    status,
)
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, defer, load_only, selectinload

from app.database import get_db
from app.deps import get_current_user, get_optional_current_user
from app.models import Problem, Submission, TestCase, User
from app.schemas import (
    CodeSubmitRequest,
    ProblemDetail,
    ProblemListItem,
    PublicTestCaseRead,
    SubmissionListItem,
    SubmissionRead,
)
from app.security import MAX_DATABASE_ID
from app.services.judge import judge_submission, submission_to_read

router = APIRouter(prefix="/api", tags=["practice"])
# Read routes retain V1's ASCII slug shape so existing Upper_Case and
# underscore slugs remain reachable. New writes use the stricter ProblemSlug.
SLUG_PATTERN = r"^[A-Za-z0-9_-]+$"
OPTIONAL_BEARER_OPENAPI = {"security": [{}, {"HTTPBearer": []}]}
ACTIVE_SUBMISSION_STATUSES = ("PENDING", "RUNNING")
MAX_ACTIVE_SUBMISSIONS_PER_USER = 3


def _problem_to_list_item(problem: Problem, accepted: bool = False) -> ProblemListItem:
    return ProblemListItem(
        id=problem.id,
        slug=problem.slug,
        title=problem.title,
        difficulty=problem.difficulty,
        tags=problem.tags,
        checker=problem.checker,
        ordered=problem.ordered,
        time_limit_ms=problem.time_limit_ms,
        is_active=problem.is_active,
        created_at=problem.created_at,
        accepted=accepted,
    )


def _accepted_problem_ids(
    db: Session, user: Optional[User], problem_ids: list[int]
) -> set[int]:
    if user is None or not problem_ids:
        return set()
    return set(
        db.scalars(
            select(Submission.problem_id)
            .where(
                Submission.user_id == user.id,
                Submission.problem_id.in_(problem_ids),
                Submission.mode == "submit",
                Submission.status == "AC",
            )
            .distinct()
        )
    )


def problem_to_detail(problem: Problem, accepted: bool = False) -> ProblemDetail:
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
        **_problem_to_list_item(problem, accepted).model_dump(),
        public_cases=public_cases,
        statement_md=problem.statement_md,
        schema_md=problem.schema_md,
    )


def submission_to_list_item(submission: Submission) -> SubmissionListItem:
    return SubmissionListItem(
        id=submission.id,
        user_id=submission.user_id,
        problem_id=submission.problem_id,
        problem_slug=submission.problem.slug,
        problem_title=submission.problem.title,
        username=submission.user.username,
        mode=submission.mode,
        status=submission.status,
        score=submission.score,
        max_score=submission.max_score,
        created_at=submission.created_at,
        completed_at=submission.completed_at,
    )


@router.get(
    "/problems",
    response_model=list[ProblemListItem],
    openapi_extra=OPTIONAL_BEARER_OPENAPI,
)
def list_problems(
    response: Response,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    search: Annotated[Optional[str], Query(min_length=1, max_length=100)] = None,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
) -> list[ProblemListItem]:
    filters = [Problem.is_active.is_(True)]
    if search is not None:
        search = search.strip()
        if "\x00" in search:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Search must not contain NUL characters",
            )
        if search:
            escaped_search = (
                search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            pattern = f"%{escaped_search}%"
            tag_values = (
                func.jsonb_array_elements_text(Problem.tags)
                if db.get_bind().dialect.name == "postgresql"
                else func.json_each(Problem.tags)
            ).table_valued("value")
            tag_matches = (
                select(1)
                .select_from(tag_values)
                .where(tag_values.c.value.ilike(pattern, escape="\\"))
                .exists()
            )
            filters.append(
                or_(
                    Problem.slug.ilike(pattern, escape="\\"),
                    Problem.title.ilike(pattern, escape="\\"),
                    tag_matches,
                )
            )

    total_count = db.scalar(select(func.count(Problem.id)).where(*filters)) or 0
    problems = list(
        db.scalars(
            select(Problem)
            .options(
                load_only(
                    Problem.id,
                    Problem.slug,
                    Problem.title,
                    Problem.difficulty,
                    Problem.tags,
                    Problem.checker,
                    Problem.ordered,
                    Problem.time_limit_ms,
                    Problem.is_active,
                    Problem.created_at,
                )
            )
            .where(*filters)
            .order_by(Problem.id)
            .offset(offset)
            .limit(limit)
        )
    )
    accepted_ids = _accepted_problem_ids(
        db, current_user, [problem.id for problem in problems]
    )
    response.headers["X-Total-Count"] = str(total_count)
    response.headers["Vary"] = "Authorization"
    return [
        _problem_to_list_item(problem, problem.id in accepted_ids)
        for problem in problems
    ]


@router.get(
    "/problems/{slug}",
    response_model=ProblemDetail,
    openapi_extra=OPTIONAL_BEARER_OPENAPI,
)
def get_problem(
    slug: Annotated[str, Path(min_length=1, max_length=80, pattern=SLUG_PATTERN)],
    response: Response,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
) -> ProblemDetail:
    problem = db.scalar(
        select(Problem)
        .options(
            selectinload(
                Problem.test_cases.and_(TestCase.is_hidden.is_(False))
            ).load_only(
                TestCase.id,
                TestCase.name,
                TestCase.score,
                TestCase.is_hidden,
                TestCase.expected_output,
                raiseload=True,
            )
        )
        .where(Problem.slug == slug, Problem.is_active.is_(True))
    )
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Problem not found",
            headers={"Vary": "Authorization"},
        )
    accepted_ids = _accepted_problem_ids(db, current_user, [problem.id])
    response.headers["Vary"] = "Authorization"
    return problem_to_detail(problem, problem.id in accepted_ids)


def _create_submission(
    slug: str,
    payload: CodeSubmitRequest,
    mode: Literal["run", "submit"],
    db: Session,
    current_user: User,
) -> SubmissionRead:
    locked_user_id = db.scalar(
        select(User.id).where(User.id == current_user.id).with_for_update()
    )
    if locked_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists",
            headers={"WWW-Authenticate": "Bearer"},
        )
    active_submission_count = db.scalar(
        select(func.count(Submission.id)).where(
            Submission.user_id == current_user.id,
            Submission.status.in_(ACTIVE_SUBMISSION_STATUSES),
        )
    )
    if (active_submission_count or 0) >= MAX_ACTIVE_SUBMISSIONS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"At most {MAX_ACTIVE_SUBMISSIONS_PER_USER} submissions may be pending or running per user"
            ),
            headers={"Retry-After": "1"},
        )

    problem = db.scalar(
        select(Problem)
        .where(Problem.slug == slug, Problem.is_active.is_(True))
        .with_for_update()
    )
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
        )

    selected_cases = select(TestCase.id).where(TestCase.problem_id == problem.id)
    if mode == "run":
        selected_cases = selected_cases.where(TestCase.is_hidden.is_(False))
    if db.scalar(selected_cases.limit(1)) is None:
        detail = (
            "Problem has no public test cases"
            if mode == "run"
            else "Problem has no test cases"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)

    submission = Submission(
        user_id=current_user.id, problem_id=problem.id, code=payload.code, mode=mode
    )
    db.add(submission)
    db.flush()
    submission_id = submission.id
    db.commit()
    # Enqueue immediately after the durable write. FastAPI BackgroundTasks run
    # only after the response has been sent, so a serialization/send failure
    # could otherwise strand the row in PENDING indefinitely.
    judge_submission(submission_id)
    db.refresh(submission)
    return submission_to_read(
        db, submission, viewer_is_admin=current_user.role == "admin"
    )


@router.post("/problems/{slug}/run", response_model=SubmissionRead)
def run_problem(
    slug: Annotated[str, Path(min_length=1, max_length=80, pattern=SLUG_PATTERN)],
    payload: CodeSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubmissionRead:
    return _create_submission(slug, payload, "run", db, current_user)


@router.post("/problems/{slug}/submit", response_model=SubmissionRead)
def submit_problem(
    slug: Annotated[str, Path(min_length=1, max_length=80, pattern=SLUG_PATTERN)],
    payload: CodeSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubmissionRead:
    return _create_submission(slug, payload, "submit", db, current_user)


@router.get("/submissions/{submission_id}", response_model=SubmissionRead)
def get_submission(
    submission_id: Annotated[int, Path(gt=0, le=MAX_DATABASE_ID)],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SubmissionRead:
    query = (
        select(Submission)
        .options(
            defer(Submission.code, raiseload=True),
            defer(Submission.error_message, raiseload=True),
            selectinload(Submission.problem).load_only(Problem.slug, Problem.title),
            selectinload(Submission.user).load_only(User.username),
        )
        .where(Submission.id == submission_id)
    )
    if current_user.role != "admin":
        query = query.where(Submission.user_id == current_user.id)
    submission = db.scalar(query)
    if submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )
    return submission_to_read(
        db, submission, viewer_is_admin=current_user.role == "admin"
    )


@router.get("/submissions", response_model=list[SubmissionListItem])
def list_submissions(
    mine: bool = Query(default=True),
    problem_slug: Optional[str] = Query(
        default=None,
        min_length=1,
        max_length=80,
        pattern=SLUG_PATTERN,
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SubmissionListItem]:
    query = select(Submission).options(
        load_only(
            Submission.id,
            Submission.user_id,
            Submission.problem_id,
            Submission.mode,
            Submission.status,
            Submission.score,
            Submission.max_score,
            Submission.created_at,
            Submission.completed_at,
        ),
        selectinload(Submission.problem).load_only(Problem.slug, Problem.title),
        selectinload(Submission.user).load_only(User.username),
    )
    if mine or current_user.role != "admin":
        query = query.where(Submission.user_id == current_user.id)
    if problem_slug:
        query = query.join(Problem).where(Problem.slug == problem_slug)
    query = query.order_by(Submission.id.desc()).limit(50)
    return [submission_to_list_item(item) for item in db.scalars(query)]
