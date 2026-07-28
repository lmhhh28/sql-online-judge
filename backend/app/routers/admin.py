import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only, selectinload
from starlette.concurrency import run_in_threadpool

from app.database import SessionLocal, get_db
from app.deps import require_admin
from app.models import Problem, Submission, TestCase, User, utc_now
from app.routers.problems import (
    _problem_to_list_item,
    problem_to_detail,
    submission_to_list_item,
)
from app.schemas import (
    ImportResponse,
    ProblemCreate,
    ProblemDetail,
    ProblemUpdate,
    SubmissionListItem,
    TestCaseCreate,
)
from app.security import MAX_DATABASE_ID
from app.services.importer import (
    MAX_ARCHIVE_SIZE,
    MAX_CASE_SCORE,
    MAX_EXPECTED_OUTPUT_BYTES,
    MAX_MEMBER_SIZE,
    MAX_PROBLEM_TEXT_LENGTH,
    MAX_TEST_CASES,
    MAX_UNCOMPRESSED_SIZE,
    ImportConflictError,
    ImportErrorDetail,
    import_problem_package,
    validate_expected_output,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
ACTIVE_SUBMISSION_STATUSES = ("PENDING", "RUNNING")
ZIP_CONTENT_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/octet-stream",
}
MAX_PROBLEM_SCORE = MAX_TEST_CASES * MAX_CASE_SCORE


async def _read_zip_request(request: Request) -> bytes:
    content_type = (
        request.headers.get("content-type", "").partition(";")[0].strip().lower()
    )
    if content_type not in ZIP_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/zip",
        )

    content_lengths = request.headers.getlist("content-length")
    if len(content_lengths) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length"
        )
    if content_lengths:
        content_length = content_lengths[0]
        if not content_length.isascii() or not content_length.isdigit():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length"
            )
        normalized_length = content_length.lstrip("0") or "0"
        maximum_length = str(MAX_ARCHIVE_SIZE)
        if len(normalized_length) > len(maximum_length) or (
            len(normalized_length) == len(maximum_length)
            and normalized_length > maximum_length
        ):
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Zip file is too large",
            )

    package = bytearray()
    async for chunk in request.stream():
        if len(package) + len(chunk) > MAX_ARCHIVE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Zip file is too large",
            )
        package.extend(chunk)
    if not package:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Zip file is empty"
        )
    return bytes(package)


def _problem_for_update(db: Session, problem_id: int) -> Problem:
    problem = db.scalar(
        select(Problem)
        .options(selectinload(Problem.test_cases))
        .where(Problem.id == problem_id)
        .with_for_update()
    )
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found"
        )
    active_submission = db.scalar(
        select(Submission.id)
        .where(
            Submission.problem_id == problem.id,
            Submission.status.in_(ACTIVE_SUBMISSION_STATUSES),
        )
        .limit(1)
    )
    if active_submission is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Problem cannot be changed while a submission is pending or running",
        )
    return problem


def _import_package(package: bytes) -> ImportResponse:
    db = SessionLocal()
    try:
        summary = import_problem_package(db, package)
        return ImportResponse(
            problem=_problem_to_list_item(summary.problem),
            imported_cases=summary.imported_cases,
        )
    finally:
        db.close()


def _validate_case_output(expected_output: str, checker: str, case_name: str) -> None:
    try:
        validate_expected_output(expected_output, checker, case_name)
    except ImportErrorDetail as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


def _case_content_size(setup_sql: str, judge_sql: str, expected_output: str) -> int:
    return sum(
        len(value.encode("utf-8")) for value in (setup_sql, judge_sql, expected_output)
    )


def _unprocessable(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=detail,
    )


def _validate_case_resource_limits(
    setup_sql: str,
    judge_sql: str,
    expected_output: str,
    case_name: str,
) -> None:
    if not judge_sql.strip():
        raise _unprocessable(f"judge_sql for test case {case_name} must not be blank")
    for field_name, value, maximum in (
        ("setup_sql", setup_sql, MAX_MEMBER_SIZE),
        ("judge_sql", judge_sql, MAX_MEMBER_SIZE),
        ("expected_output", expected_output, MAX_EXPECTED_OUTPUT_BYTES),
    ):
        if len(value.encode("utf-8")) > maximum:
            raise _unprocessable(
                f"{field_name} for test case {case_name} exceeds the {maximum}-byte limit"
            )


def _validate_active_problem(problem: Problem, checker: str) -> None:
    if not problem.test_cases:
        raise _unprocessable("An active problem must contain at least one test case")
    if len(problem.test_cases) > MAX_TEST_CASES:
        raise _unprocessable(
            f"An active problem may contain at most {MAX_TEST_CASES} test cases"
        )

    for field_name in ("statement_md", "schema_md"):
        value = getattr(problem, field_name)
        if (
            len(value) > MAX_PROBLEM_TEXT_LENGTH
            or len(value.encode("utf-8")) > MAX_PROBLEM_TEXT_LENGTH
        ):
            raise _unprocessable(
                f"{field_name} exceeds the {MAX_PROBLEM_TEXT_LENGTH}-byte limit"
            )

    total_content_size = 0
    scores: list[float] = []
    for case in problem.test_cases:
        _validate_case_resource_limits(
            case.setup_sql,
            case.judge_sql,
            case.expected_output,
            case.name,
        )
        _validate_case_output(case.expected_output, checker, case.name)
        total_content_size += _case_content_size(
            case.setup_sql,
            case.judge_sql,
            case.expected_output,
        )
        if total_content_size > MAX_UNCOMPRESSED_SIZE:
            raise _unprocessable(
                "Test case content exceeds the per-problem limit "
                f"of {MAX_UNCOMPRESSED_SIZE // (1024 * 1024)} MiB"
            )
        if not math.isfinite(case.score) or not 0 <= case.score <= MAX_CASE_SCORE:
            raise _unprocessable(f"Test case {case.name} has an invalid score")
        scores.append(case.score)

    try:
        total_score = math.fsum(scores)
    except OverflowError as exc:
        raise _unprocessable("The total test case score is too large") from exc
    if not math.isfinite(total_score) or total_score > MAX_PROBLEM_SCORE:
        raise _unprocessable(
            f"The total test case score may not exceed {MAX_PROBLEM_SCORE:g}"
        )


@router.post(
    "/problems/import",
    response_model=ImportResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/zip": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def import_problem(
    request: Request,
    _: User = Depends(require_admin),
) -> ImportResponse:
    package = await _read_zip_request(request)
    try:
        return await run_in_threadpool(_import_package, package)
    except ImportConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ImportErrorDetail as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Problem slug already exists"
        ) from exc


@router.post("/problems", response_model=ProblemDetail)
def create_problem(
    payload: ProblemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProblemDetail:
    if payload.is_active:
        raise _unprocessable(
            "A problem cannot be activated until it contains at least one test case"
        )
    if db.scalar(select(Problem.id).where(Problem.slug == payload.slug)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Problem slug already exists"
        )
    problem = Problem(**payload.model_dump())
    try:
        db.add(problem)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Problem slug already exists"
        ) from exc
    db.refresh(problem)
    return problem_to_detail(problem)


@router.put("/problems/{problem_id}", response_model=ProblemDetail)
def update_problem(
    problem_id: Annotated[int, Path(gt=0, le=MAX_DATABASE_ID)],
    payload: ProblemUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProblemDetail:
    problem = _problem_for_update(db, problem_id)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(problem, key, value)
    if problem.is_active:
        _validate_active_problem(problem, problem.checker)
    problem.updated_at = utc_now()
    db.commit()
    db.refresh(problem)
    return problem_to_detail(problem)


@router.post("/problems/{problem_id}/test-cases", response_model=ProblemDetail)
def add_test_case(
    problem_id: Annotated[int, Path(gt=0, le=MAX_DATABASE_ID)],
    payload: TestCaseCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProblemDetail:
    problem = _problem_for_update(db, problem_id)
    if any(case.name == payload.name for case in problem.test_cases):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Test case name already exists"
        )
    if len(problem.test_cases) >= MAX_TEST_CASES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Problem may contain at most {MAX_TEST_CASES} test cases",
        )
    _validate_case_resource_limits(
        payload.setup_sql,
        payload.judge_sql,
        payload.expected_output,
        payload.name,
    )
    _validate_case_output(payload.expected_output, problem.checker, payload.name)
    existing_content_size = sum(
        _case_content_size(case.setup_sql, case.judge_sql, case.expected_output)
        for case in problem.test_cases
    )
    new_content_size = _case_content_size(
        payload.setup_sql, payload.judge_sql, payload.expected_output
    )
    if existing_content_size + new_content_size > MAX_UNCOMPRESSED_SIZE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Test case content exceeds the per-problem limit "
                f"of {MAX_UNCOMPRESSED_SIZE // (1024 * 1024)} MiB"
            ),
        )
    problem.test_cases.append(TestCase(**payload.model_dump()))
    problem.updated_at = utc_now()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Test case name already exists"
        ) from exc
    db.refresh(problem)
    return problem_to_detail(problem)


@router.get("/submissions", response_model=list[SubmissionListItem])
def admin_submissions(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[SubmissionListItem]:
    query = (
        select(Submission)
        .options(
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
        .order_by(Submission.id.desc())
        .limit(100)
    )
    return [submission_to_list_item(submission) for submission in db.scalars(query)]
