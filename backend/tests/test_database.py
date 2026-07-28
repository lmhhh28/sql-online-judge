from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import database


class _RevisionResult:
    def __init__(self, revisions):
        self.revisions = revisions

    def scalars(self):
        return self

    def all(self):
        return self.revisions


class _Connection:
    def __init__(self, revisions):
        self.revisions = revisions

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _statement):
        return _RevisionResult(self.revisions)


class _Engine:
    def __init__(self, dialect_name, revisions=()):
        self.dialect = SimpleNamespace(name=dialect_name)
        self.revisions = revisions

    def connect(self):
        return _Connection(self.revisions)


def test_schema_check_accepts_all_heads_and_skips_non_postgresql(monkeypatch):
    monkeypatch.setattr(database, "_expected_schema_heads", lambda: {"head-a"})

    database.verify_database_schema_current(_Engine("postgresql", ["head-a"]))
    database.verify_database_schema_current(_Engine("sqlite"))


def test_schema_check_rejects_stale_database_with_upgrade_instruction(monkeypatch):
    monkeypatch.setattr(database, "_expected_schema_heads", lambda: {"head-b"})

    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        database.verify_database_schema_current(_Engine("postgresql", ["head-a"]))


def test_schema_check_reports_unavailable_revision_table(monkeypatch):
    class BrokenEngine(_Engine):
        def connect(self):
            raise OperationalError("connect", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(database, "_expected_schema_heads", lambda: {"head"})

    with pytest.raises(RuntimeError, match="Could not verify"):
        database.verify_database_schema_current(BrokenEngine("postgresql"))
