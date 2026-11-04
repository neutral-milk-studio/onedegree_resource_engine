import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app.main import create_app
from shared.hsds_time import to_hsds_time
from shared.hsds_validation import find_issues

UUID_VALUE = "ac148810-d857-441c-9679-408f346de14b"


def minimal(**extra):
    return {"id": UUID_VALUE, "name": "Example", "status": "active", **extra}


def paths(record) -> list[str]:
    return [issue.path for issue in find_issues(record, "service")]


def test_valid_records_pass_including_nested_objects_and_extension():
    record = minimal(
        organization={"id": UUID_VALUE, "name": "Org", "description": "Text"},
        schedules=[{"id": UUID_VALUE, "opens_at": "09:00-08:00"}],
        x_resource_engine={"note": ""},
    )
    assert paths(record) == []


@pytest.mark.parametrize(
    ("record", "path"),
    [
        (minimal(id="211bayarea-1"), "id"),
        (minimal(url="not a url"), "url"),
        (minimal(email="nope"), "email"),
        (minimal(last_modified="yesterday"), "last_modified"),
        (minimal(description=None), "description"),
        (minimal(description=""), "description"),
        (minimal(status="open"), "status"),
        (minimal(minimum_age="18"), "minimum_age"),
        (minimal(schedules=[{"id": UUID_VALUE, "freq": "DAILY"}]), "schedules/0/freq"),
        (minimal(schedules=[{"id": UUID_VALUE, "opens_at": "09:00"}]), "schedules/0/opens_at"),
        (minimal(organization={"id": UUID_VALUE, "name": "Org"}), "organization"),
        (minimal(capacities=[{"id": UUID_VALUE}]), "capacities/0"),
    ],
)
def test_invalid_records_are_rejected(record, path):
    assert path in paths(record)


def test_seed_records_are_valid():
    data = json.loads(Path("seed/hsds_dataset.json").read_text())
    for entity, key in (("organization", "organizations"), ("service", "services")):
        for record in data[key]:
            assert find_issues(record, entity) == []


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 3, 7), "08:30-08:00"),
        (date(2026, 3, 8), "08:30-07:00"),
        (date(2026, 10, 31), "08:30-07:00"),
        (date(2026, 11, 1), "08:30-08:00"),
    ],
)
def test_hours_follow_daylight_saving(day, expected):
    assert to_hsds_time("08:30", "America/Los_Angeles", day) == expected


def test_app_starts_and_enforces_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/health").json()["hsds_version"] == "3.2.3"
    assert client.get("/hsds/services").status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert client.get("/hsds/services", headers=wrong).status_code == 401
    assert client.get("/api/admin/stats", headers=wrong).status_code == 401
