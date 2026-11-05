"""Cross-source conflicts on the real ODRS + NDP samples: detect, list, resolve."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app
from ingest.pipeline.conflicts import detect_conflicts, domain, name_key
from shared.hsds_validation import find_issues

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
def client(loaded_store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    monkeypatch.setenv("RE_DEV_USER", "sam")
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    return TestClient(app)


def test_normalizers():
    assert domain("http://www.coastsidehope.org/") == domain("https://coastsidehope.org")
    assert name_key("Stanford Community Law Clinic (CLC)") == name_key(
        "stanford community law clinic"
    )


def test_ingest_detects_the_real_coastside_hope_conflict(client):
    conflicts = client.get("/conflicts", headers=AUTH).json()
    # Coastside Hope is in both samples. Websites match once normalized and the two share a
    # phone number, so the only real disagreement is the email address.
    assert [(c["organization_name"], c["field"]) for c in conflicts] == [
        ("Coastside Hope", "email")
    ]
    [c] = conflicts
    assert {c["a"]["source"], c["b"]["source"]} == {"ndp", "odrs"}
    ndp = c["a"] if c["a"]["source"] == "ndp" else c["b"]
    assert ndp["value"] == "admin@coastsidehope.org" and len(ndp["services"]) >= 2
    assert client.get("/metrics", headers=AUTH).json()["conflicts_open"] == 1


def test_detection_is_idempotent(loaded_store):
    again = detect_conflicts(loaded_store)
    assert again.opened == 0 and again.refreshed == 1


def test_resolve_applies_winner_to_losing_source_with_history(client):
    [c] = client.get("/conflicts", headers=AUTH).json()
    ndp_side = "a" if c["a"]["source"] == "ndp" else "b"
    odrs = c["b"] if ndp_side == "a" else c["a"]
    response = client.post(f"/conflicts/{c['id']}/resolve", headers=AUTH, json={"choice": ndp_side})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["conflict"]["status"] == "resolved" and body["services_updated"] == 1
    record = client.get(f"/services/{odrs['services'][0]['id']}", headers=AUTH).json()
    assert record["email"] == "admin@coastsidehope.org"
    assert record["metadata"][-1]["field_name"] == "email" and find_issues(record, "service") == []
    audit = client.get("/audit?limit=1", headers=AUTH).json()[0]
    assert (audit["action"], audit["change_source"], audit["actor"]) == (
        "resolve_conflict", "conflict", "sam",
    )  # fmt: skip
    assert client.get("/conflicts", headers=AUTH).json() == []

    client.post("/ingest/odrs", headers=AUTH)  # re-ingest keeps the chosen value
    record = client.get(f"/services/{odrs['services'][0]['id']}", headers=AUTH).json()
    assert record["email"] == "admin@coastsidehope.org"
    assert client.get("/conflicts", headers=AUTH).json() == []


def test_custom_value_updates_both_sources(client):
    [c] = client.get("/conflicts", headers=AUTH).json()
    body = {"choice": "custom", "custom_value": "help@coastsidehope.org"}
    result = client.post(f"/conflicts/{c['id']}/resolve", headers=AUTH, json=body).json()
    assert result["services_updated"] >= 3


def test_resolve_errors(client):
    [c] = client.get("/conflicts", headers=AUTH).json()
    url = f"/conflicts/{c['id']}/resolve"
    bad = client.post(url, headers=AUTH, json={"choice": "custom", "custom_value": "not an email"})
    assert bad.status_code == 422
    assert client.post(url, headers=AUTH, json={"choice": "custom"}).status_code == 422
    assert client.post(url, headers=AUTH, json={"choice": "a"}).status_code == 200
    assert client.post(url, headers=AUTH, json={"choice": "a"}).status_code == 409
    assert (
        client.post("/conflicts/nope/resolve", headers=AUTH, json={"choice": "a"}).status_code
        == 404
    )


def test_conflict_endpoints_require_token(client):
    assert client.get("/conflicts").status_code in {401, 403}
    assert client.post("/conflicts/detect").status_code in {401, 403}


def test_suspicious_listing_in_review_queue_can_be_hidden(client):
    client.post("/maintenance/run", headers=AUTH)
    queue = client.get("/review/findings?category=suspicious", headers=AUTH).json()["contents"]
    assert len(queue) == 2  # the fake rent relief listing and Quick Rent Rescue
    [flag] = [f for f in queue if f["service_name"] == "Emergency Rent Relief Grants"]
    assert len(flag["risk_signals"]) == 5 and flag["harm_rank"] == 1
    body = {"expected_version": flag["record_version"], "edited_value": "inactive"}
    result = client.post(f"/review/findings/{flag['id']}/apply", headers=AUTH, json=body)
    assert result.status_code == 200
    record = client.get(f"/services/{flag['service_id']}", headers=AUTH).json()
    assert record["status"] == "inactive"
