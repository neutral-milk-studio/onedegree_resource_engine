"""API: bearer auth, HSDS Page shape, served records valid, ingest endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app
from shared.hsds_validation import find_issues

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", TOKEN)
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


@pytest.fixture
def loaded(client):
    assert client.post("/ingest/odrs", headers=AUTH).status_code == 200
    return client


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/"),
        ("get", "/services"),
        ("get", "/services/00000000-0000-0000-0000-000000000000"),
        ("post", "/ingest/odrs"),
        ("get", "/ingest/runs"),
    ],
)
@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}]
)
def test_every_endpoint_requires_the_bearer_token(client, method, path, headers):
    response = getattr(client, method)(path, headers=headers)
    assert response.status_code in {401, 403}
    assert "contents" not in response.text


def test_missing_server_token_rejects_everyone(client, monkeypatch):
    monkeypatch.delenv("RE_BEARER_TOKEN")
    assert client.get("/services", headers=AUTH).status_code == 401


def test_health_is_open(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_empty_store_returns_empty_page(client):
    body = client.get("/services", headers=AUTH).json()
    assert body["empty"] and body["contents"] == [] and body["total_items"] == 0


def test_ingest_then_list_valid_hsds_pages(loaded):
    body = loaded.get("/services?per_page=20", headers=AUTH).json()
    assert body["total_items"] == 50 and body["total_pages"] == 3
    assert body["first_page"] and not body["last_page"] and len(body["contents"]) == 20
    assert all(find_issues(r, "service") == [] for r in body["contents"])
    last = loaded.get("/services?per_page=20&page=3", headers=AUTH).json()
    assert last["last_page"] and len(last["contents"]) == 10


def test_get_service_and_404(loaded):
    first = loaded.get("/services?per_page=1", headers=AUTH).json()["contents"][0]
    record = loaded.get(f"/services/{first['id']}", headers=AUTH).json()
    assert record["id"] == first["id"] and find_issues(record, "service") == []
    missing = loaded.get("/services/00000000-0000-0000-0000-000000000000", headers=AUTH)
    assert missing.status_code == 404


def test_bad_parameters_name_the_parameter(client):
    response = client.get("/services?page=0", headers=AUTH)
    assert response.status_code == 422 and "page" in response.text
    assert client.get("/services/not-a-uuid", headers=AUTH).status_code == 422


def test_filters_combine(loaded):
    first = loaded.get("/services?per_page=1", headers=AUTH).json()["contents"][0]
    org = first["organization_id"]
    body = loaded.get(f"/services?organization_id={org}&source=odrs", headers=AUTH).json()
    assert body["total_items"] >= 1
    assert all(r["organization_id"] == org for r in body["contents"])
    none = loaded.get(f"/services?organization_id={org}&source=ndp", headers=AUTH).json()
    assert none["empty"]


def test_ingest_runs_report_counts(loaded):
    [run] = loaded.get("/ingest/runs", headers=AUTH).json()
    assert run["received"] == run["stored"] + run["quarantined"] == 50
    assert run["status"] == "succeeded" and run["elapsed_ms"] >= 0
    held = loaded.get(f"/ingest/runs/{run['id']}/quarantine", headers=AUTH)
    assert held.status_code == 200 and held.json() == []


def test_unknown_source_is_404(client):
    response = client.post("/ingest/nope", headers=AUTH)
    assert response.status_code == 404 and "configured sources" in response.json()["detail"]
