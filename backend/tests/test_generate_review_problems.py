from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg.conninfo import conninfo_to_dict

GENERATOR_PATH = (
    Path(__file__).resolve().parents[2] / "tools" / "generate_review_problems.py"
)
GENERATOR_SPEC = importlib.util.spec_from_file_location(
    "sql_oj_generate_review_problems", GENERATOR_PATH
)
assert GENERATOR_SPEC is not None and GENERATOR_SPEC.loader is not None
generator = importlib.util.module_from_spec(GENERATOR_SPEC)
sys.modules[GENERATOR_SPEC.name] = generator
GENERATOR_SPEC.loader.exec_module(generator)


def test_generator_conninfo_is_bounded_and_rejects_indirect_options(monkeypatch):
    for variable in generator.FORBIDDEN_LIBPQ_ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(
        "ADMIN_DATABASE_URL",
        "postgresql:///postgres?host=/tmp&connect_timeout=9999",
    )

    parameters = conninfo_to_dict(generator.admin_conninfo())

    assert parameters["connect_timeout"] == "5"

    monkeypatch.setenv("PGOPTIONS", "-c role=unsafe")
    with pytest.raises(RuntimeError, match="PGOPTIONS"):
        generator.admin_conninfo()


def test_generator_cleanup_does_not_mask_primary_failure(monkeypatch, capsys):
    class BrokenConnection:
        def __enter__(self):
            raise ValueError("primary generation failure")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(generator, "create_database", lambda _name: None)
    monkeypatch.setattr(
        generator,
        "psycopg",
        SimpleNamespace(connect=lambda *_args, **_kwargs: BrokenConnection()),
    )

    def fail_drop(_name):
        raise RuntimeError("secondary cleanup failure")

    monkeypatch.setattr(generator, "drop_database", fail_drop)
    problem = generator.ProblemSpec("slug", "title", "", [], "", "", "select 1", [])

    with pytest.raises(ValueError, match="primary generation failure"):
        generator.expected_output(problem, generator.TestSpec("case", ""))

    assert "secondary cleanup failure" in capsys.readouterr().err


def test_generator_attempts_cleanup_when_database_creation_has_uncertain_result(
    monkeypatch, capsys
):
    def fail_create(_name):
        raise generator.DatabaseCreationOutcomeUnknown(
            "database creation result is unknown"
        )

    def fail_drop(_name):
        raise RuntimeError("cleanup connection also failed")

    monkeypatch.setattr(generator, "create_database", fail_create)
    monkeypatch.setattr(generator, "drop_database", fail_drop)
    problem = generator.ProblemSpec("slug", "title", "", [], "", "", "select 1", [])

    with pytest.raises(
        generator.DatabaseCreationOutcomeUnknown,
        match="database creation result is unknown",
    ):
        generator.expected_output(problem, generator.TestSpec("case", ""))

    assert "cleanup connection also failed" in capsys.readouterr().err


def test_generator_does_not_drop_database_after_definitive_duplicate(monkeypatch):
    fixed_uuid = "a" * 32
    drop_calls = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _statement):
            raise generator.psycopg.errors.DuplicateDatabase("database already exists")

    monkeypatch.setattr(
        generator.uuid, "uuid4", lambda: SimpleNamespace(hex=fixed_uuid)
    )
    monkeypatch.setattr(generator, "admin_conninfo", lambda *_args: "dbname=postgres")
    monkeypatch.setattr(
        generator.psycopg,
        "connect",
        lambda *_args, **_kwargs: Connection(),
    )
    monkeypatch.setattr(generator, "drop_database", drop_calls.append)
    problem = generator.ProblemSpec("slug", "title", "", [], "", "", "select 1", [])

    with pytest.raises(generator.psycopg.errors.DuplicateDatabase):
        generator.expected_output(problem, generator.TestSpec("case", ""))

    assert drop_calls == []


def test_create_database_classifies_post_send_disconnect_as_unknown(monkeypatch):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _statement):
            raise generator.psycopg.OperationalError("connection lost")

    monkeypatch.setattr(generator, "admin_conninfo", lambda: "dbname=postgres")
    monkeypatch.setattr(
        generator.psycopg,
        "connect",
        lambda *_args, **_kwargs: Connection(),
    )

    with pytest.raises(generator.DatabaseCreationOutcomeUnknown):
        generator.create_database("oj_pkg_" + "b" * 20)


def test_create_database_keeps_pre_send_connection_failure_definitive(monkeypatch):
    def fail_connect(*_args, **_kwargs):
        raise generator.psycopg.OperationalError("could not connect")

    monkeypatch.setattr(generator, "admin_conninfo", lambda: "dbname=postgres")
    monkeypatch.setattr(generator.psycopg, "connect", fail_connect)

    with pytest.raises(generator.psycopg.OperationalError):
        generator.create_database("oj_pkg_" + "c" * 20)


def test_generator_installs_complete_output_with_real_atomic_exchange(tmp_path):
    output = tmp_path / "review-2026"
    staging = tmp_path / ".staging"
    output.mkdir()
    (output / "old").write_text("old", encoding="utf-8")
    staging.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")

    generator.install_generated_output(staging, output)

    assert not (output / "old").exists()
    assert (output / "new").read_text(encoding="utf-8") == "new"
    assert not staging.exists()


def test_generator_exchange_failure_preserves_output_and_staging(monkeypatch, tmp_path):
    output = tmp_path / "review-2026"
    staging = tmp_path / ".staging"
    output.mkdir()
    (output / "old").write_text("old", encoding="utf-8")
    staging.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")

    def fail_exchange(_first, _second):
        raise RuntimeError("atomic exchange unavailable")

    monkeypatch.setattr(generator, "_atomic_exchange_directories", fail_exchange)

    with pytest.raises(RuntimeError, match="atomic exchange unavailable"):
        generator.install_generated_output(staging, output)

    assert (output / "old").read_text(encoding="utf-8") == "old"
    assert (staging / "new").read_text(encoding="utf-8") == "new"


def test_generator_rejects_unsupported_atomic_exchange(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "first").write_text("first", encoding="utf-8")
    (second / "second").write_text("second", encoding="utf-8")
    monkeypatch.setattr(generator.sys, "platform", "unsupported-test-platform")

    with pytest.raises(RuntimeError, match="not supported"):
        generator._atomic_exchange_directories(first, second)

    assert (first / "first").read_text(encoding="utf-8") == "first"
    assert (second / "second").read_text(encoding="utf-8") == "second"


def test_generator_first_install_is_one_rename(monkeypatch, tmp_path):
    output = tmp_path / "review-2026"
    staging = tmp_path / ".staging"
    staging.mkdir()
    (staging / "new").write_text("new", encoding="utf-8")

    def unexpected_exchange(_first, _second):
        raise AssertionError("first install must not require directory exchange")

    monkeypatch.setattr(generator, "_atomic_exchange_directories", unexpected_exchange)

    generator.install_generated_output(staging, output)

    assert (output / "new").read_text(encoding="utf-8") == "new"
    assert not staging.exists()


def test_main_preserves_complete_staging_when_install_fails(monkeypatch, tmp_path):
    examples = tmp_path / "examples"
    output = examples / "review-2026"
    output.mkdir(parents=True)
    (output / "preserved").write_text("old", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "OUTPUT", output)
    monkeypatch.setattr(
        generator, "problem_specs", lambda: [SimpleNamespace(slug="complete")]
    )

    def write_complete_problem(problem, staging):
        problem_dir = staging / "problems" / problem.slug
        problem_dir.mkdir()
        (problem_dir / "new").write_text("new", encoding="utf-8")

    def fail_install(_staging, _output):
        raise RuntimeError("atomic exchange failed")

    monkeypatch.setattr(generator, "write_problem", write_complete_problem)
    monkeypatch.setattr(generator, "write_readme", lambda _specs, _output: None)
    monkeypatch.setattr(generator, "install_generated_output", fail_install)

    with pytest.raises(RuntimeError, match="atomic exchange failed"):
        generator.main()

    assert (output / "preserved").read_text(encoding="utf-8") == "old"
    staging_paths = list(examples.glob(".review-2026.staging-*"))
    assert len(staging_paths) == 1
    assert (staging_paths[0] / "problems" / "complete" / "new").read_text(
        encoding="utf-8"
    ) == "new"


def test_main_does_not_misreport_success_when_old_tree_cleanup_fails(
    monkeypatch, tmp_path, capsys
):
    examples = tmp_path / "examples"
    output = examples / "review-2026"
    output.mkdir(parents=True)
    (output / "old").write_text("old", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "OUTPUT", output)
    monkeypatch.setattr(
        generator, "problem_specs", lambda: [SimpleNamespace(slug="complete")]
    )

    def write_complete_problem(problem, staging):
        problem_dir = staging / "problems" / problem.slug
        problem_dir.mkdir()
        (problem_dir / "new").write_text("new", encoding="utf-8")

    def fail_cleanup(_path):
        raise PermissionError("old tree is read-only")

    monkeypatch.setattr(generator, "write_problem", write_complete_problem)
    monkeypatch.setattr(generator, "write_readme", lambda _specs, _output: None)
    monkeypatch.setattr(generator.shutil, "rmtree", fail_cleanup)

    generator.main()

    assert (output / "problems" / "complete" / "new").read_text(
        encoding="utf-8"
    ) == "new"
    staging_paths = list(examples.glob(".review-2026.staging-*"))
    assert len(staging_paths) == 1
    assert (staging_paths[0] / "old").read_text(encoding="utf-8") == "old"
    captured = capsys.readouterr()
    assert "old tree is read-only" in captured.err
    assert "Generated 1 problems" in captured.out


def test_main_cleans_its_partial_staging_when_child_mkdir_fails(monkeypatch, tmp_path):
    examples = tmp_path / "examples"
    output = examples / "review-2026"
    output.mkdir(parents=True)
    (output / "preserved").write_text("old", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "OUTPUT", output)
    monkeypatch.setattr(generator, "problem_specs", lambda: [])
    original_mkdir = generator.Path.mkdir

    def fail_zips_mkdir(path, *args, **kwargs):
        if path.name == "zips":
            raise OSError("could not create zips directory")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(generator.Path, "mkdir", fail_zips_mkdir)

    with pytest.raises(OSError, match="could not create zips directory"):
        generator.main()

    assert (output / "preserved").read_text(encoding="utf-8") == "old"
    assert not list(examples.glob(".review-2026.staging-*"))


def test_main_uuid_collision_does_not_touch_existing_staging(monkeypatch, tmp_path):
    examples = tmp_path / "examples"
    output = examples / "review-2026"
    output.mkdir(parents=True)
    (output / "preserved").write_text("old", encoding="utf-8")
    existing_staging = examples / ".review-2026.staging-collision"
    existing_staging.mkdir()
    (existing_staging / "retained").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "OUTPUT", output)
    monkeypatch.setattr(generator, "problem_specs", lambda: [])
    monkeypatch.setattr(
        generator.uuid, "uuid4", lambda: SimpleNamespace(hex="collision")
    )

    with pytest.raises(FileExistsError):
        generator.main()

    assert (output / "preserved").read_text(encoding="utf-8") == "old"
    assert (existing_staging / "retained").read_text(encoding="utf-8") == "keep"


def test_failed_generation_preserves_previous_output(monkeypatch, tmp_path):
    examples = tmp_path / "examples"
    output = examples / "review-2026"
    output.mkdir(parents=True)
    (output / "preserved").write_text("old", encoding="utf-8")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "OUTPUT", output)
    monkeypatch.setattr(
        generator, "problem_specs", lambda: [SimpleNamespace(slug="broken")]
    )

    def fail_generation(*_args):
        raise ValueError("generation failed")

    monkeypatch.setattr(generator, "write_problem", fail_generation)

    with pytest.raises(ValueError, match="generation failed"):
        generator.main()

    assert (output / "preserved").read_text(encoding="utf-8") == "old"
    assert not list(examples.glob(".review-2026.staging-*"))
