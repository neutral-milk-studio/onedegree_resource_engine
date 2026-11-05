"""Need coverage by city: which INTENTS needs are missing in each city, over the real
sample data (loaded_store) -- no mocks, since this is just a read of what's ingested."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app
from search.query.coverage import coverage_by_city
from search.query.intent import INTENTS

AUTH = {"Authorization": "Bearer test-token"}


def test_every_city_reports_every_need_as_covered_or_missing_once(loaded_store):
    rows = coverage_by_city(loaded_store)
    assert rows  # the sample data has cities
    for row in rows:
        assert row.service_count > 0
        assert set(row.covered) | set(row.missing) == set(INTENTS)
        assert set(row.covered) & set(row.missing) == set()


def test_food_is_covered_somewhere_in_the_sample_data(loaded_store):
    rows = coverage_by_city(loaded_store)
    assert any("Food" in row.covered for row in rows)


def test_coverage_endpoint(loaded_store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    client = TestClient(app)

    response = client.get("/coverage", headers=AUTH)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["needs"]) == set(INTENTS)
    assert body["cities"]
    for city in body["cities"]:
        assert set(city["covered"]) | set(city["missing"]) == set(INTENTS)


def test_coverage_requires_auth(loaded_store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    client = TestClient(app)

    assert client.get("/coverage").status_code in {401, 403}
