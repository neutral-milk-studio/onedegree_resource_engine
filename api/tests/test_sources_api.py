"""Add a partner API: upload -> suggested mapping -> confirm -> counts and quarantine reasons."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app

AUTH = {"Authorization": "Bearer test-token"}
SAMPLE = Path(__file__).parents[2] / "data/samples/spreadsheet/county_food_pantries.csv"


@pytest.fixture
def client(store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    monkeypatch.setenv("RE_DEV_USER", "sam")
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: store
    return TestClient(app)


def upload(client, content=None, name="county_food_pantries.csv"):
    content = SAMPLE.read_bytes() if content is None else content
    return client.post(
        "/sources/preview", headers=AUTH, files={"file": (name, content, "text/csv")}
    )


def test_preview_suggests_mapping_with_confidence(client):
    body = upload(client).json()
    assert body["rows"] == 7 and body["model_id"] == "offline-rules"
    info = next(c for c in body["columns"] if c["column"] == "Info")
    assert info["target"] == "packed" and info["confidence"] >= 0.9
    assert info["samples"][0].startswith("Hours: Mon-Fri")
    assert body["targets"]["packed"] == "Several fields in one column"


@pytest.mark.parametrize(
    "content, name, status",
    [(b"", "x.csv", 422), (b"a,b\n", "x.csv", 422), (b"a\n1\n", "x.xlsx", 415),
     (b"a,a\n1,2\n", "x.csv", 422)],
)  # fmt: skip
def test_preview_rejects_bad_files_clearly(client, content, name, status):
    response = upload(client, content, name)
    assert response.status_code == status and response.json()["detail"]


def test_confirm_imports_and_reports_counts_and_reasons(client):
    preview = upload(client).json()
    mapping = {c["column"]: c["target"] for c in preview["columns"]}
    body = {"upload_id": preview["upload_id"], "partner_name": "Solano Food Pantries",
            "mapping": mapping}  # fmt: skip
    response = client.post("/sources/confirm", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"] == "partner_solano-food-pantries"
    run = result["run"]
    assert (run["received"], run["stored"], run["quarantined"]) == (7, 7, 0)
    assert run["objects_quarantined"] == 2 and run["elapsed_ms"] >= 0
    reasons = sorted(q["reasons"][0] for q in result["quarantine"])
    assert "address_1" in reasons[0] and "contains no digits" in reasons[1]

    services = client.get(f"/services?source={result['source']}", headers=AUTH).json()
    assert services["total_items"] == 7
    sources = client.get("/sources", headers=AUTH).json()
    assert {"name": result["source"], "kind": "partner"}.items() <= next(
        s for s in sources if s["kind"] == "partner"
    ).items()
    audit = client.get("/audit?limit=5", headers=AUTH).json()
    assert any(e["action"] == "partner_mapping_confirmed" for e in audit)

    rerun = client.post(f"/ingest/{result['source']}", headers=AUTH)  # re-runnable, idempotent
    assert rerun.status_code == 200 and rerun.json()["received"] == 7
    assert (
        client.get(f"/services?source={result['source']}", headers=AUTH).json()["total_items"] == 7
    )


def test_edited_mapping_is_used_and_audited(client):
    preview = upload(client).json()
    mapping = {c["column"]: c["target"] for c in preview["columns"]}
    mapping["Web"] = "ignore"
    body = {"upload_id": preview["upload_id"], "partner_name": "Solano", "mapping": mapping}
    result = client.post("/sources/confirm", headers=AUTH, json=body).json()
    record = client.get(f"/services?source={result['source']}&per_page=1", headers=AUTH).json()
    assert "website" not in record["contents"][0]["organization"]
    entry = next(
        e
        for e in client.get("/audit", headers=AUTH).json()
        if e["action"] == "partner_mapping_confirmed"
    )
    assert "1 suggestion(s) changed" in entry["summary"]


@pytest.mark.parametrize(
    "change, status, message",
    [
        ({"partner_name": "  "}, 422, "name"),
        ({"partner_name": "odrs-but-a-partner", "upload_id": "nope"}, 404, "Upload the file again"),
        ({"mapping": {"Pantry Name": "service_name"}}, 422, "every column"),
        ({"mapping_all": "ignore"}, 422, "Organization name or Service name"),
        ({"mapping_bad": True}, 422, "Unknown field"),
        ({"default_state": "California"}, 422, "two capital letters"),
    ],
)
def test_confirm_validates_the_mapping(client, change, status, message):
    preview = upload(client).json()
    mapping = {c["column"]: c["target"] for c in preview["columns"]}
    if change.pop("mapping_all", None):
        mapping = dict.fromkeys(mapping, "ignore")
    if change.pop("mapping_bad", None):
        mapping["Web"] = "favourite_colour"
    body = {
        "upload_id": preview["upload_id"],
        "partner_name": "Solano",
        "mapping": mapping,
        **change,
    }
    response = client.post("/sources/confirm", headers=AUTH, json=body)
    assert response.status_code == status and message in str(response.json()["detail"])


def test_sample_and_auth(client):
    assert client.get("/sources/sample", headers=AUTH).text.startswith("Pantry Name,Run By")
    assert client.get("/sources").status_code in {401, 403}
    no_auth = client.post("/sources/preview", files={"file": ("x.csv", b"a\n1\n", "text/csv")})
    assert no_auth.status_code in {401, 403}
