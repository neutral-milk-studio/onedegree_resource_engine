"""ODRS mapper and end-to-end transform against the sample export."""

import copy
import json
from pathlib import Path

import pytest

from transform import odrs
from transform.hsds import hsds_id, validate
from transform.run import run, split
from transform.store import Store

SAMPLE = Path(__file__).parents[1] / "data/samples/ODRS/BayesExport_20250729.json"


@pytest.fixture(scope="module")
def page():
    return json.loads(SAMPLE.read_text())


@pytest.fixture(scope="module")
def record(page):
    return next(r for r in odrs.records(page) if r["id"] == 22560)


def by_kind(objects, kind):
    return [o for k, o in objects if k == kind]


def test_every_sample_record_maps_to_valid_hsds(page):
    for rec in odrs.records(page):
        for kind, obj in odrs.map(rec):
            assert validate(kind, obj) == [], (rec["id"], kind, obj)


def test_service_fields(record):
    objects = odrs.map(record)
    [svc] = by_kind(objects, "service")
    assert svc["id"] == hsds_id("odrs", "service", 22560)
    assert svc["name"].startswith("Get emergency financial assistance")
    assert svc["status"] == "active"
    assert svc["assured_date"] == "2025-06-13"
    assert svc["x_resource_engine"]["source"] == {"system": "odrs", "record_id": "22560"}
    [org] = by_kind(objects, "organization")
    assert svc["organization_id"] == org["id"]


def test_org_description_fallback_is_flagged(record):
    [org] = by_kind(odrs.map(record), "organization")
    assert org["description"] == "Bay Area Community Services (BACS). Description not available."
    assert "missing_information:description" in org["x_resource_engine"]["flags"]


def test_links_between_objects(record):
    objects = odrs.map(record)
    ids = {o["id"] for _, o in objects}
    for _, obj in objects:
        for field in ("organization_id", "service_id", "location_id", "service_at_location_id"):
            if field in obj:
                assert obj[field] in ids, (field, obj)


def test_unavailable_at_means_inactive(record):
    rec = copy.deepcopy(record)
    rec["unavailable_at"] = "2025-07-01T00:00:00.000Z"
    [svc] = by_kind(odrs.map(rec), "service")
    assert svc["status"] == "inactive"


def test_age_strings_become_numbers(page):
    rec = next(r for r in odrs.records(page) if r["properties"].get("elig-age-or-over") == "13")
    [svc] = by_kind(odrs.map(rec), "service")
    assert (svc["minimum_age"], svc["maximum_age"]) == (13, 18)


def test_phone_extension_split(page):
    phones = [o for r in odrs.records(page) for k, o in odrs.map(r) if k == "phone"]
    assert {"number": "650-276-4101", "extension": 4}.items() <= next(p for p in phones if p.get("extension") == 4).items()
    assert all("x" not in p["number"] for p in phones)


def test_schedule_groups_days_and_keeps_notes():
    rec = {
        "id": 1, "title": "T", "organization": {"id": 2, "name": "O"},
        "schedule": {
            "monday_start": "09:00", "monday_end": "17:00",
            "tuesday_start": "09:00", "tuesday_end": "12:00",
            "wednesday_start": "09:00", "wednesday_end": "17:00",
            "saturday_start": "", "saturday_end": "",
            "notes": "Closed daily 12:00 PM - 1:00 PM.",
        },
    }
    schedules = sorted(by_kind(odrs.map(rec), "schedule"), key=lambda s: s["byday"])
    assert [(s["byday"], s["opens_at"], s["closes_at"]) for s in schedules] == [
        ("MO,WE", "09:00", "17:00"), ("TU", "09:00", "12:00"),
    ]
    assert all(s["notes"] == "Closed daily 12:00 PM - 1:00 PM." for s in schedules)


def test_notes_only_schedule():
    rec = {"id": 1, "title": "T", "organization": {"id": 2, "name": "O"}, "schedule": {"notes": "By appointment."}}
    [sched] = by_kind(odrs.map(rec), "schedule")
    assert sched["notes"] == "By appointment." and "byday" not in sched


def test_split_cascades_to_children(record):
    objects = odrs.map(record)
    for kind, obj in objects:
        if kind == "service":
            obj["status"] = "open"  # break the service
    good, bad = split(objects)
    bad_kinds = {k for k, _, _ in bad}
    assert {"service", "phone", "service_at_location", "schedule"} <= bad_kinds
    # The organization and locations don't depend on the service, so they survive.
    assert {"organization", "location", "address"} <= {k for k, _ in good}


def test_end_to_end_counts_and_idempotence(page):
    store = Store()
    first = run(odrs, [page], store, "run-1")
    assert first["records"] == 50
    assert first["objects"] == first["kept"] + first["quarantined"]
    assert first["quarantined"] == 0

    kept = store.kept()
    assert all(validate(kind, body) == [] for kind, body in kept)
    # Shared organizations and locations are stored once, not once per opportunity.
    assert len(store.kept("organization")) == 41
    assert len(store.kept("location")) == 67

    run(odrs, [page], store, "run-2")
    assert store.kept() == kept  # same ids, no duplicates


def test_bad_record_is_quarantined_not_fatal(page):
    store = Store()
    broken = {"opportunities": [{"id": 999}, *odrs.records(page)[:2]]}  # no title, no organization
    result = run(odrs, [broken], store, "run-x")
    assert result["records"] == 3 and result["records_quarantined"] == 1
    [q] = store.quarantined("run-x")
    assert q["kind"] == "record" and q["native_id"] == "999"


def test_child_with_missing_parent_is_quarantined():
    # A phone pointing at a service that is neither in this batch nor stored.
    phone = {"id": hsds_id("odrs", "phone", 1), "number": "555-0100", "service_id": hsds_id("odrs", "service", 404)}
    good, bad = split([("phone", phone)])
    assert good == [] and "does not exist" in bad[0][2][0]
    # ...but it is fine when the parent is already stored.
    good, bad = split([("phone", phone)], exists=lambda kind, id: kind == "service")
    assert bad == []


def test_tabular_required_is_enforced():
    svc = {"id": hsds_id("odrs", "service", 1), "name": "S", "status": "active"}  # valid JSON HSDS
    assert validate("service", svc) == ["(root): 'organization_id' is required when stored (tabular_required)"]
