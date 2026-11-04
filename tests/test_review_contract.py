import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from openapi_spec_validator import validate as validate_openapi
from pydantic import ValidationError

from contracts.export_openapi import OPENAPI_PATH, render
from contracts.mock_api import MOCK_REVIEWER, create_app
from contracts.review import Clash, FieldRef, ReviewEvidence, ReviewFinding
from shared.hsds_validation import find_issues

FIXTURES = Path("contracts/fixtures")
AUTH = {"Authorization": "Bearer test-token"}
NOW = datetime(2026, 11, 3, tzinfo=UTC)


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


NDP_SERVICE = load("coastside_hope.ndp.hsds.json")["id"]
ODRS_SERVICE = load("coastside_hope.odrs.hsds.json")["id"]


@pytest.fixture
def client():
    return TestClient(create_app())


def version_of(client, service_id=NDP_SERVICE) -> int:
    return client.get(f"/review/records/{service_id}", headers=AUTH).json()["version"]


def first_finding(client, category: str) -> dict:
    url = f"/review/findings?category={category}"
    return client.get(url, headers=AUTH).json()["contents"][0]


@pytest.mark.parametrize("name", ["coastside_hope.ndp.hsds.json", "coastside_hope.odrs.hsds.json"])
def test_expected_hsds_fixtures_are_valid(name):
    assert find_issues(load(name), "service") == []


def test_finding_needs_evidence_and_model_id():
    service_id = uuid4()
    base = {
        "id": uuid4(),
        "service_id": service_id,
        "ref": FieldRef(entity_type="service", entity_id=service_id, field="email"),
        "category": "discrepancy",
        "confidence": 0.8,
        "created_at": NOW,
    }
    quote = ReviewEvidence(
        kind="quote", source_type="website", url="https://x.invalid", fetched_at=NOW, snippet="text"
    )
    with pytest.raises(ValidationError):
        ReviewFinding(**base, evidence=[])
    with pytest.raises(ValidationError):
        ReviewFinding(**base, evidence=[quote])
    with pytest.raises(ValidationError):
        ReviewEvidence(kind="quote", source_type="website", url="https://x.invalid", fetched_at=NOW)
    with pytest.raises(ValidationError):
        ReviewFinding(**{**base, "category": "verified"}, evidence=[quote], model_id="model")
    assert ReviewFinding(**base, evidence=[quote], model_id="model")


def test_dead_site_finding_is_possible_without_a_quote():
    service_id = uuid4()
    signal = ReviewEvidence(
        kind="http_signal",
        source_type="website",
        url="https://x.invalid",
        fetched_at=NOW,
        http_status=404,
    )
    assert ReviewFinding(
        id=uuid4(),
        service_id=service_id,
        ref=FieldRef(entity_type="service", entity_id=service_id, field="status"),
        category="actively_bad",
        evidence=[signal],
        confidence=0.6,
        created_at=NOW,
    )


def test_finding_quotes_appear_in_the_seed_page():
    page = (FIXTURES / "website_seed/coastside_hope_services.html").read_text()
    quotes = [
        evidence["snippet"]
        for finding in load("findings.json")
        for evidence in finding["evidence"]
        if evidence["kind"] == "quote"
    ]
    assert quotes
    assert all(quote in page for quote in quotes)


def test_fixture_findings_and_clashes_match_records():
    ids = {NDP_SERVICE, ODRS_SERVICE}
    findings = [ReviewFinding.model_validate(item) for item in load("findings.json")]
    clashes = [Clash.model_validate(item) for item in load("clashes.json")]
    assert {str(item.service_id) for item in findings + clashes} <= ids


def test_openapi_file_is_current_and_valid():
    assert OPENAPI_PATH.read_text() == render()
    validate_openapi(yaml.safe_load(render()))


def test_mock_api_requires_bearer_token(client):
    assert client.get("/services").status_code in {401, 403}


def test_list_uses_hsds_page_and_valid_records(client):
    body = client.get("/services", headers=AUTH).json()
    assert body["total_items"] == 2
    assert body["first_page"] and body["last_page"] and not body["empty"]
    assert all(find_issues(record, "service") == [] for record in body["contents"])


def test_apply_finding_updates_record_with_history_from_authenticated_reviewer(client):
    finding = first_finding(client, "discrepancy")
    response = client.post(
        f"/review/findings/{finding['id']}/apply",
        headers=AUTH,
        json={"expected_version": version_of(client)},
    )
    assert response.status_code == 200
    assert response.json()["review_state"] == "applied"

    record = client.get(f"/services/{NDP_SERVICE}", headers=AUTH).json()
    assert "La Honda" in [area["name"] for area in record["service_areas"]]
    assert record["metadata"][-1]["updated_by"] == MOCK_REVIEWER
    assert find_issues(record, "service") == []


def test_stale_version_is_rejected_and_not_saved(client):
    change = {
        "ref": {"entity_type": "service", "entity_id": NDP_SERVICE, "field": "wait_time"},
        "value": "Two weeks",
    }
    url = f"/review/records/{NDP_SERVICE}"
    current = version_of(client)
    body = {"expected_version": current, "changes": [change]}
    assert client.patch(url, headers=AUTH, json=body).status_code == 200
    stale = {"expected_version": current, "changes": [{**change, "value": "Three weeks"}]}
    assert client.patch(url, headers=AUTH, json=stale).status_code == 409
    record = client.get(f"/services/{NDP_SERVICE}", headers=AUTH).json()
    assert record["wait_time"] == "Two weeks"


def test_finding_without_proposal_needs_an_edited_value(client):
    finding = first_finding(client, "actively_bad")
    url = f"/review/findings/{finding['id']}/apply"
    version = version_of(client, ODRS_SERVICE)
    assert client.post(url, headers=AUTH, json={"expected_version": version}).status_code == 422
    response = client.post(
        url, headers=AUTH, json={"expected_version": version, "edited_value": "inactive"}
    )
    assert response.json()["review_state"] == "edited"


def test_invalid_edit_is_blocked_and_not_saved(client):
    change = {
        "ref": {"entity_type": "service", "entity_id": NDP_SERVICE, "field": "status"},
        "value": "open",
    }
    response = client.patch(
        f"/review/records/{NDP_SERVICE}",
        headers=AUTH,
        json={"expected_version": version_of(client), "changes": [change]},
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "status"
    assert client.get(f"/services/{NDP_SERVICE}", headers=AUTH).json()["status"] == "active"


def test_resolving_a_clash_keeps_the_chosen_value(client):
    clash = client.get("/review/clashes?status=open", headers=AUTH).json()["contents"][0]
    response = client.post(
        f"/review/clashes/{clash['id']}/resolve",
        headers=AUTH,
        json={"expected_version": version_of(client), "choice": "current"},
    )
    assert response.json()["status"] == "resolved"
    record = client.get(f"/services/{NDP_SERVICE}", headers=AUTH).json()
    assert record["eligibility_description"] == clash["current_value"]
