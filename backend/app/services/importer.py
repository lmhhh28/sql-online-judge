from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Problem, TestCase


class ImportErrorDetail(ValueError):
    pass


@dataclass(frozen=True)
class ImportSummary:
    problem: Problem
    imported_cases: int


def _read_text(zip_file: ZipFile, path: str, required: bool = True) -> str:
    try:
        with zip_file.open(path) as file:
            return file.read().decode("utf-8")
    except KeyError:
        if required:
            raise ImportErrorDetail(f"Missing required file: {path}") from None
        return ""


def _list_test_dirs(zip_file: ZipFile) -> list[str]:
    dirs: set[str] = set()
    for name in zip_file.namelist():
        path = PurePosixPath(name)
        if len(path.parts) >= 3 and path.parts[0] == "tests":
            dirs.add("/".join(path.parts[:2]))
    return sorted(dirs)


def import_problem_package(db: Session, package_bytes: bytes) -> ImportSummary:
    try:
        zip_file = ZipFile(BytesIO(package_bytes))
    except BadZipFile as exc:
        raise ImportErrorDetail("Uploaded file is not a valid zip") from exc

    with zip_file:
        metadata_text = _read_text(zip_file, "problem.yaml")
        try:
            metadata = yaml.safe_load(metadata_text) or {}
        except yaml.YAMLError as exc:
            raise ImportErrorDetail(f"Invalid problem.yaml: {exc}") from exc

        slug = str(metadata.get("slug", "")).strip()
        title = str(metadata.get("title", "")).strip()
        if not slug or not title:
            raise ImportErrorDetail("problem.yaml must include slug and title")

        checker = str(metadata.get("checker", "result"))
        if checker not in {"result", "string"}:
            raise ImportErrorDetail("checker must be result or string")

        statement_md = _read_text(zip_file, "statement.md")
        schema_md = _read_text(zip_file, "schema.md", required=False)

        problem = db.scalar(select(Problem).where(Problem.slug == slug))
        if problem is None:
            problem = Problem(slug=slug, title=title)
            db.add(problem)

        problem.title = title
        problem.statement_md = statement_md
        problem.schema_md = schema_md
        problem.difficulty = str(metadata.get("difficulty", "入门"))
        problem.tags = list(metadata.get("tags", []) or [])
        problem.checker = checker
        problem.ordered = bool(metadata.get("ordered", False))
        problem.time_limit_ms = int(metadata.get("time_limit_ms", 3000))
        problem.is_active = bool(metadata.get("is_active", True))

        problem.test_cases.clear()
        imported_cases = 0
        for test_dir in _list_test_dirs(zip_file):
            name = test_dir.split("/", 1)[1]
            setup_sql = _read_text(zip_file, f"{test_dir}/setup.sql", required=False)
            judge_sql = _read_text(zip_file, f"{test_dir}/judge.sql")
            expected_output = _read_text(zip_file, f"{test_dir}/expected.csv")
            problem.test_cases.append(
                TestCase(
                    name=name,
                    is_hidden=not name.startswith("sample_"),
                    score=float(metadata.get("case_score", 1.0)),
                    setup_sql=setup_sql,
                    judge_sql=judge_sql,
                    expected_output=expected_output,
                )
            )
            imported_cases += 1

        if imported_cases == 0:
            raise ImportErrorDetail("Package must include at least one test case under tests/")

        db.commit()
        db.refresh(problem)
        return ImportSummary(problem=problem, imported_cases=imported_cases)

