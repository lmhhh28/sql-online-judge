import json
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas import (
    ProblemListItem,
    SubmissionListItem,
    SubmissionRead,
    UserRead,
)
from app.schemas import TestResultRead as ResultRead


def test_response_datetimes_serialize_as_unambiguous_utc_without_changing_python_dump():
    naive_utc = datetime(2026, 7, 28, 12, 34, 56)
    user = UserRead(id=1, username="time-user", role="user", created_at=naive_utc)

    assert user.model_dump()["created_at"] == naive_utc
    assert json.loads(user.model_dump_json())["created_at"] == "2026-07-28T12:34:56Z"

    aware = UserRead(
        id=2,
        username="aware-user",
        role="user",
        created_at=datetime(
            2026, 7, 28, 20, 34, 56, tzinfo=timezone(timedelta(hours=8))
        ),
    )
    assert json.loads(aware.model_dump_json())["created_at"] == ("2026-07-28T12:34:56Z")

    created_at_schema = UserRead.model_json_schema(mode="serialization")["properties"][
        "created_at"
    ]
    assert created_at_schema == {
        "format": "date-time",
        "title": "Created At",
        "type": "string",
    }


@pytest.mark.parametrize(
    "model",
    [UserRead, ProblemListItem, ResultRead, SubmissionRead, SubmissionListItem],
)
def test_all_response_created_at_fields_keep_datetime_schema(model):
    created_at_schema = model.model_json_schema(mode="serialization")["properties"][
        "created_at"
    ]
    assert created_at_schema["type"] == "string"
    assert created_at_schema["format"] == "date-time"


@pytest.mark.parametrize("model", [SubmissionRead, SubmissionListItem])
def test_optional_completed_at_fields_keep_datetime_schema(model):
    completed_at_schema = model.model_json_schema(mode="serialization")["properties"][
        "completed_at"
    ]
    assert any(
        variant.get("format") == "date-time" for variant in completed_at_schema["anyOf"]
    )
