from psycopg.conninfo import conninfo_to_dict

from app.config import settings
from app.services.judge import _admin_conninfo, _merge_status


def test_admin_conninfo_replaces_database_without_query_string(monkeypatch):
    monkeypatch.setattr(settings, "admin_database_url", "postgresql+psycopg://user:secret@localhost/postgres")

    params = conninfo_to_dict(_admin_conninfo("oj_run_test"))

    assert params["dbname"] == "oj_run_test"
    assert params["user"] == "user"
    assert params["password"] == "secret"
    assert params["host"] == "localhost"


def test_admin_conninfo_preserves_unix_socket_host(monkeypatch):
    monkeypatch.setattr(settings, "admin_database_url", "postgresql+psycopg://lmhhh@/postgres?host=/tmp")

    params = conninfo_to_dict(_admin_conninfo("oj_run_test"))

    assert params["dbname"] == "oj_run_test"
    assert params["user"] == "lmhhh"
    assert params["host"] == "/tmp"


def test_merge_status_keeps_most_severe_status():
    status = "AC"
    for candidate in ["WA", "AC", "RE", "TLE", "CE"]:
        status = _merge_status(status, candidate)

    assert status == "TLE"
    assert _merge_status("WA", "SE") == "SE"
