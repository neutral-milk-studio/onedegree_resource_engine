"""Review API: harm-ordered queue, apply (HSDS re-validated, metadata, audit), keep current,
stale versions, invalid values, re-ingest keeps fixes, and metrics after checks."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.app.deps import get_store
from api.app.main import create_app
from shared.hsds_validation import find_issues

AUTH = {"Authorization": "Bearer test-token"}
PUENTE = "d02029d4-b4d2-5c9d-8c59-947b76dff913"
FREE_THE_NEED = "0ef857e6-7d0f-5f11-849f-72d0a18abefa"
BLOCKED = "db60e548-6cd2-59cc-b241-4abcd29f443e"
DEAD = "2b18ff78-ba42-5e96-a5a5-5e414a395bca"


@pytest.fixture
def client(loaded_store, raw_store, monkeypatch):
    monkeypatch.setenv("RE_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("RE_RAW_DIR", str(raw_store.root))
    monkeypatch.setenv("RE_DEV_USER", "sam")
    app = create_app(init_database=False)
    app.dependency_overrides[get_store] = lambda: loaded_store
    c = TestClient(app)
    report = c.post("/maintenance/run", headers=AUTH).json()
    assert report["checked"] == 13 and report["model_id"] == "offline-rules"
    return c


def queue(client, **params):
    return client.get("/review/findings", headers=AUTH, params=params).json()


def finding_for(client, service_id):
    return next(f for f in queue(client)["contents"] if f["service_id"] == service_id)


@pytest.mark.parametrize(
    "method, path",
    [("post", "/maintenance/run"), ("get", "/maintenance/checks"), ("get", "/review/findings"),
     ("post", "/review/findings/x/apply"), ("post", "/review/findings/x/dismiss")],
)  # fmt: skip
def test_review_endpoints_require_token(client, method, path):
    kwargs = {"json": {}} if method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code in {401, 403}


def test_queue_is_sorted_by_harm_and_filterable(client):
    body = queue(client)
    ranks = [f["harm_rank"] for f in body["contents"]]
    assert ranks == sorted(ranks) and body["total_items"] == 9
    assert body["contents"][0]["category"] == "actively_bad"
    assert body["contents"][-1]["category"] == "check_failed"
    assert body["counts_by_category"] == {
        "actively_bad": 1, "suspicious": 2, "discrepancy": 3, "missing_information": 2,
        "check_failed": 1,
    }  # fmt: skip
    only = queue(client, category="discrepancy")["contents"]
    assert {f["category"] for f in only} == {"discrepancy"} and len(only) == 3
    assert body["contents"][1]["category"] == "suspicious"
    assert body["contents"][2]["category"] == "suspicious"
    first = body["contents"][3]
    assert first["field"] in {"phone", "hours"}  # contact discrepancies outrank the rest
    assert first["trust"]["badge"].startswith("1 open issue")
    assert first["evidence"][0]["kind"] == "quote" and first["evidence"][0]["snippet"]
    dead = body["contents"][0]["evidence"][0]
    assert dead["kind"] == "http_signal" and "NXDOMAIN" in dead["error"]


def test_apply_phone_updates_record_with_history_and_audit(client):
    finding = finding_for(client, PUENTE)
    body = {"expected_version": finding["record_version"], "seconds_to_fix": 14.2}
    response = client.post(f"/review/findings/{finding['id']}/apply", headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["finding"]["review_state"] == "applied"
    assert result["record_version"] == finding["record_version"] + 1
    audit = result["audit_entry"]
    assert (audit["actor"], audit["field"], audit["change_source"]) == ("sam", "phone", "ai")
    assert (audit["old_value"], audit["new_value"]) == ("(650) 879-1691", "(650) 879-1700")
    assert "14 s" in audit["summary"]

    record = client.get(f"/services/{PUENTE}", headers=AUTH).json()
    assert find_issues(record, "service") == []
    assert record["phones"][0]["number"] == "(650) 879-1700"
    assert record["organization"]["phones"][0]["number"] == "(650) 879-1700"
    meta = record["metadata"][-1]
    assert (meta["field_name"], meta["previous_value"], meta["updated_by"]) == (
        "phone", "(650) 879-1691", "sam",
    )  # fmt: skip
    assert PUENTE not in {f["service_id"] for f in queue(client)["contents"]}
    history = client.get(f"/review/records/{PUENTE}/history", headers=AUTH).json()
    assert history[0]["action"] == "apply_finding"


def test_apply_hours_serves_valid_hsds_with_offsets(client):
    finding = finding_for(client, FREE_THE_NEED)
    client.post(
        f"/review/findings/{finding['id']}/apply",
        headers=AUTH,
        json={"expected_version": finding["record_version"]},
    )
    record = client.get(f"/services/{FREE_THE_NEED}", headers=AUTH).json()
    [sched] = record["schedules"]
    assert sched["byday"] == "FR" and sched["opens_at"].startswith("12:00-0")
    assert find_issues(record, "service") == []


def test_stale_version_is_rejected_and_nothing_saved(client):
    finding = finding_for(client, PUENTE)
    stale = {"expected_version": finding["record_version"] - 1}
    response = client.post(f"/review/findings/{finding['id']}/apply", headers=AUTH, json=stale)
    assert response.status_code == 409 and "changed this record" in response.json()["detail"]
    record = client.get(f"/services/{PUENTE}", headers=AUTH).json()
    assert record["phones"][0]["number"] == "(650) 879-1691"


def test_invalid_edited_value_is_422_with_paths_and_not_saved(client):
    finding = finding_for(client, PUENTE)
    body = {"expected_version": finding["record_version"], "edited_value": "call the front desk"}
    response = client.post(f"/review/findings/{finding['id']}/apply", headers=AUTH, json=body)
    assert response.status_code == 422
    assert response.json()["detail"][0]["path"] == "phone"
    assert finding_for(client, PUENTE)["review_state"] == "pending"


def test_edited_value_is_recorded_as_a_specialist_change(client):
    finding = finding_for(client, PUENTE)
    body = {"expected_version": finding["record_version"], "edited_value": "(650) 879-1702"}
    result = client.post(f"/review/findings/{finding['id']}/apply", headers=AUTH, json=body).json()
    assert result["finding"]["review_state"] == "edited"
    assert result["audit_entry"]["change_source"] == "specialist"


def test_dead_site_needs_a_chosen_status(client):
    finding = finding_for(client, DEAD)
    url = f"/review/findings/{finding['id']}/apply"
    version = finding["record_version"]
    assert client.post(url, headers=AUTH, json={"expected_version": version}).status_code == 422
    ok = client.post(
        url, headers=AUTH, json={"expected_version": version, "edited_value": "inactive"}
    )
    assert ok.status_code == 200
    assert client.get(f"/services/{DEAD}", headers=AUTH).json()["status"] == "inactive"


def test_check_failed_can_only_be_kept(client):
    finding = finding_for(client, BLOCKED)
    version = finding["record_version"]
    url = f"/review/findings/{finding['id']}"
    assert (
        client.post(f"{url}/apply", headers=AUTH, json={"expected_version": version}).status_code
        == 422
    )
    kept = client.post(f"{url}/dismiss", headers=AUTH, json={"reason": "Will call them"})
    assert kept.status_code == 200
    assert kept.json()["finding"]["review_state"] == "dismissed"
    assert "kept the current" in kept.json()["audit_entry"]["summary"]
    again = client.post(f"{url}/dismiss", headers=AUTH, json={})
    assert again.status_code == 409


FAKE_RENT_RELIEF = "e885691b-3136-58c2-82e5-533f924116e4"
QUICK_RENT_RESCUE = "6e2b7a6d-f8fb-56fe-ac4f-c25ff14bf40b"


def test_hide_removes_from_queue_without_changing_the_record(client):
    finding = finding_for(client, FAKE_RENT_RELIEF)
    before = client.get(f"/services/{FAKE_RENT_RELIEF}", headers=AUTH).json()
    response = client.post(
        f"/review/findings/{finding['id']}/hide", headers=AUTH, json={"seconds_to_fix": 9}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["finding"]["review_state"] == "hidden"
    audit = result["audit_entry"]
    assert audit["action"] == "apply_finding" or "hid" in audit["summary"]
    assert "hid" in audit["summary"] and "Emergency Rent Relief Grants" in audit["summary"]
    assert FAKE_RENT_RELIEF not in {f["service_id"] for f in queue(client)["contents"]}
    after = client.get(f"/services/{FAKE_RENT_RELIEF}", headers=AUTH).json()
    assert after["status"] == before["status"]


def test_mark_safe_requires_a_note(client):
    finding = finding_for(client, FAKE_RENT_RELIEF)
    empty = client.post(
        f"/review/findings/{finding['id']}/mark-safe", headers=AUTH, json={"note": ""}
    )
    assert empty.status_code == 422
    assert FAKE_RENT_RELIEF in {f["service_id"] for f in queue(client)["contents"]}

    ok = client.post(
        f"/review/findings/{finding['id']}/mark-safe",
        headers=AUTH,
        json={"note": "Called them, this is real", "seconds_to_fix": 12},
    )
    assert ok.status_code == 200, ok.text
    result = ok.json()
    assert result["finding"]["review_state"] == "marked_safe"
    assert "Called them, this is real" in result["audit_entry"]["summary"]
    assert FAKE_RENT_RELIEF not in {f["service_id"] for f in queue(client)["contents"]}


def test_mark_safe_stops_the_next_check_from_reflagging(client):
    finding = finding_for(client, FAKE_RENT_RELIEF)
    client.post(
        f"/review/findings/{finding['id']}/mark-safe",
        headers=AUTH,
        json={"note": "Verified with the org directly"},
    )
    rerun = client.post(
        "/maintenance/run", headers=AUTH, json={"service_ids": [FAKE_RENT_RELIEF]}
    ).json()
    assert rerun["findings_created"] == 0
    assert FAKE_RENT_RELIEF not in {f["service_id"] for f in queue(client)["contents"]}


def test_confirm_and_remove_applies_inactive_status(client):
    finding = finding_for(client, QUICK_RENT_RESCUE)
    response = client.post(
        f"/review/findings/{finding['id']}/apply",
        headers=AUTH,
        json={"expected_version": finding["record_version"], "edited_value": "inactive"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["finding"]["review_state"] in {"applied", "edited"}
    assert client.get(f"/services/{QUICK_RENT_RESCUE}", headers=AUTH).json()["status"] == "inactive"


def test_queue_findings_never_expose_signals_through_the_public_search(client):
    finding = finding_for(client, FAKE_RENT_RELIEF)
    assert finding["risk_signals"] and finding["risk_score"] is not None
    body = client.get("/search", headers=AUTH, params={"q": "emergency rent relief grants"}).json()
    for result in body["results"]:
        assert "risk_signals" not in result and "risk_score" not in result


def test_reingest_keeps_the_specialist_fix(client):
    finding = finding_for(client, PUENTE)
    client.post(
        f"/review/findings/{finding['id']}/apply",
        headers=AUTH,
        json={"expected_version": finding["record_version"]},
    )
    assert client.post("/ingest/ndp", headers=AUTH).status_code == 200
    record = client.get(f"/services/{PUENTE}", headers=AUTH).json()
    assert record["phones"][0]["number"] == "(650) 879-1700"
    assert record["metadata"][-1]["field_name"] == "phone"
    assert find_issues(record, "service") == []

    rerun = client.post("/maintenance/run", headers=AUTH, json={"service_ids": [PUENTE]}).json()
    assert rerun["by_category"] == {"verified": 1} and rerun["findings_created"] == 0


def test_metrics_after_checks_and_a_fix(client):
    finding = finding_for(client, PUENTE)
    client.post(
        f"/review/findings/{finding['id']}/apply",
        headers=AUTH,
        json={"expected_version": finding["record_version"], "seconds_to_fix": 20},
    )
    m = client.get("/metrics", headers=AUTH).json()
    f = m["findings"]
    assert f["checks_run"] == 13 and f["records_checked"] == 13 and f["decided"] == 1
    assert f["open_by_category"]["discrepancy"] == 2 and f["open_total"] == 8
    assert f["open_by_category"]["verified"] == 5
    assert f["median_seconds_to_fix"] == 20 and f["model_ids"] == ["offline-rules"]
    assert m["cost_per_check_usd"] == 0.0 and 0 < m["avg_trust_score"] <= 100
