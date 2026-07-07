from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import require_admin
from app.models import Problem, Submission, TestCase, User
from app.routers.problems import problem_to_detail
from app.schemas import (
    ImportResponse,
    ProblemCreate,
    ProblemDetail,
    ProblemUpdate,
    SubmissionRead,
    TestCaseCreate,
)
from app.services.importer import ImportErrorDetail, import_problem_package
from app.services.judge import submission_to_read

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/problems/import", response_model=ImportResponse)
async def import_problem(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ImportResponse:
    package = await file.read()
    try:
        summary = import_problem_package(db, package)
    except ImportErrorDetail as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    problem = db.scalar(
        select(Problem).options(selectinload(Problem.test_cases)).where(Problem.id == summary.problem.id)
    )
    assert problem is not None
    return ImportResponse(problem=problem_to_detail(problem), imported_cases=summary.imported_cases)


@router.post("/problems", response_model=ProblemDetail)
def create_problem(
    payload: ProblemCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProblemDetail:
    if db.scalar(select(Problem.id).where(Problem.slug == payload.slug)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Problem slug already exists")
    problem = Problem(**payload.model_dump())
    db.add(problem)
    db.commit()
    db.refresh(problem)
    return problem_to_detail(problem)


@router.put("/problems/{problem_id}", response_model=ProblemDetail)
def update_problem(
    problem_id: int,
    payload: ProblemUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProblemDetail:
    problem = db.scalar(select(Problem).options(selectinload(Problem.test_cases)).where(Problem.id == problem_id))
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(problem, key, value)
    db.commit()
    db.refresh(problem)
    return problem_to_detail(problem)


@router.post("/problems/{problem_id}/test-cases", response_model=ProblemDetail)
def add_test_case(
    problem_id: int,
    payload: TestCaseCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProblemDetail:
    problem = db.scalar(select(Problem).options(selectinload(Problem.test_cases)).where(Problem.id == problem_id))
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")
    problem.test_cases.append(TestCase(**payload.model_dump()))
    db.commit()
    db.refresh(problem)
    return problem_to_detail(problem)


@router.get("/submissions", response_model=list[SubmissionRead])
def admin_submissions(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[SubmissionRead]:
    query = (
        select(Submission)
        .options(selectinload(Submission.results), selectinload(Submission.problem), selectinload(Submission.user))
        .order_by(Submission.id.desc())
        .limit(100)
    )
    return [submission_to_read(db, submission, viewer_is_admin=True) for submission in db.scalars(query)]

