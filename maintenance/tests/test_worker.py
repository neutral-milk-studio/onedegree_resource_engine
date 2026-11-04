"""The whole check on the shared seed: page -> extract -> compare -> change request -> applied."""

import json

import pytest

from api.tests.helpers import make_container
from contracts.findings import Category, Evidence, Finding, FindingRejected
from maintenance.extractor.extract import ModelReply
from maintenance.findings_store.change_requests import ChangeRequestSink, structured_value
from maintenance.tests.test_detection import BAYVIEW_REPLY
from maintenance.worker.run_check import run_checks
from tools.sample_data_generator.seed import load_shared_seed

BAYVIEW = "2d4f6a8c-0b1d-5e3f-9a7c-1b3d5f7a9c0e"
EASTSIDE = "3e5a7c9d-1f2b-5a4c-8d6e-2c4e6a8b0d1f"
HARBOR = "4f6b8d0e-2a3c-5b5d-9e7f-3d5f7a9b1c2e"


def seeded(reply=BAYVIEW_REPLY):
    c = make_container()
    load_shared_seed(c)
    c.maintenance.invoke = lambda _system, _user: ModelReply(reply, "model-x")
    return c


def test_bayview_runs_from_page_to_applied_record_and_history():
    c = seeded()
    result = c.maintenance.run(scope="service", service_id=BAYVIEW)
    assert result["discrepancy"] == 1
    pending = c.proposals.list(status="pending", resource_id=BAYVIEW)
    target = next(cr for cr in pending if "eligibility_description" in cr["patch"])
    assert target["proposed_by_type"] == "ai"
    assert target["evidence"][0]["snippet"] and target["evidence"][0]["model"] == "model-x"
    assert c.store.get(BAYVIEW)["version"] == 1  # detection changed nothing

    c.proposals.approve(target["id"], "admin@example.org")
    row = c.store.get(BAYVIEW)
    assert "San Mateo" in row["data"]["eligibility_description"]
    entry = next(
        h for h in c.store.history(BAYVIEW) if h["field_name"] == "eligibility_description"
    )
    assert entry["updated_by"] == "ai-maintenance" and "admin@example.org" in entry["note"]


def test_a_failed_site_is_reported_not_skipped():
    c = seeded()
    result = c.maintenance.run(scope="service", service_id=HARBOR)
    assert result["check_failed"] + result["actively_bad"] == 1
    assert c.proposals.list(status="pending", resource_id=HARBOR)


def test_verified_updates_the_check_and_proposes_nothing():
    c = seeded(json.dumps({}))
    c.maintenance.run(scope="service", service_id=EASTSIDE)
    check = c.db.one("SELECT * FROM check_result WHERE resource_id=?", (EASTSIDE,))
    assert check["category"] == "verified"
    assert not c.proposals.list(resource_id=EASTSIDE)


def test_a_website_with_no_sample_page_is_not_called_dead():
    c = make_container()
    load_shared_seed(c)
    row = c.store.get(BAYVIEW)
    c.db.run(
        "UPDATE resource SET data=? WHERE id=?",
        (json.dumps({**row["data"], "url": "https://elsewhere.example.org"}), BAYVIEW),
    )
    c.maintenance.run(scope="service", service_id=BAYVIEW)
    check = c.db.one("SELECT * FROM check_result WHERE resource_id=?", (BAYVIEW,))
    assert check["category"] == "check_failed" and "sample" in check["summary"]
    assert not c.proposals.list(resource_id=BAYVIEW)


def test_run_checks_runs_due_records_once():
    c = seeded(json.dumps({}))
    assert run_checks(c.maintenance)["checked"] == 3
    assert run_checks(c.maintenance)["checked"] == 0


def test_list_fields_become_hsds_objects():
    phones = structured_value("phones", "(415) 555-0101", [], BAYVIEW)
    assert phones[0]["number"] == "(415) 555-0101" and phones[0]["service_id"] == BAYVIEW
    assert structured_value("eligibility_description", "text", "old", BAYVIEW) == "text"


def test_a_phone_already_on_the_record_is_not_proposed_again():
    stored = [{"id": "x", "number": "510-555-0110"}]
    phones = structured_value("phones", "510-555-0110, (510) 555-0158", stored, BAYVIEW)
    assert [item["number"] for item in phones] == ["510-555-0110", "(510) 555-0158"]


def test_sink_refuses_a_finding_without_evidence():
    finding = Finding(
        "id", BAYVIEW, "name", "a", "b", Category.DISCREPANCY, 0.9, "m", "2026-01-01T00:00:00Z", []
    )
    with pytest.raises(FindingRejected):
        ChangeRequestSink(None, None).save(finding)


def test_sink_flags_a_proposal_that_is_not_valid_hsds():
    c = make_container()
    load_shared_seed(c)
    finding = Finding(
        "id",
        BAYVIEW,
        "status",
        "active",
        "not a real status",
        Category.DISCREPANCY,
        0.9,
        "m",
        "2026-01-01T00:00:00Z",
        [Evidence("https://example.org", "quote from the page", "m")],
    )
    cid = c.maintenance.sink.save(finding)
    assert "edit before applying" in c.proposals.get(cid)["title"]
