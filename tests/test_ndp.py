"""NDP mapper and end-to-end transform against the two sample pages."""

import json
from collections import Counter
from pathlib import Path

import pytest

from transform import ndp
from transform.hsds import hsds_id, validate
from transform.run import run
from transform.store import Store

SAMPLES = sorted((Path(__file__).parents[1] / "data/samples/NDP").glob("*.json"))


@pytest.fixture(scope="module")
def pages():
    return [json.loads(p.read_text()) for p in SAMPLES]


@pytest.fixture(scope="module")
def records(pages):
    return [r for page in pages for r in ndp.records(page)]


def find(records, native_id):
    return next(r for r in records if r["id"] == native_id)


def by_kind(objects, kind):
    return [o for k, o in objects if k == kind]


def test_organization_keeps_ndp_description_and_strips_html(records):
    objects = ndp.map(find(records, "211bayarea-10595231"))  # Extra Food
    [org] = by_kind(objects, "organization")
    assert org["description"].startswith("Nonprofit agency delivering excess food")
    assert org["alternate_name"] == "FOOD RUNNERS"
    assert "flags" not in org["x_resource_engine"]  # no fallback needed, unlike ODRS
    svc = by_kind(objects, "service")[0]
    assert "<p>" not in svc["description"]
    assert svc["status"] == "active"
    assert svc["last_modified"].endswith("T00:00:00Z")


def test_weekday_schedule_is_grouped(records):
    objects = ndp.map(find(records, "211bayarea-10595231"))
    schedules = by_kind(objects, "schedule")
    assert any(s["byday"] == "MO,TU,WE,TH,FR" and s["opens_at"] == "08:00" for s in schedules)


def test_addresses_types_and_country(records):
    addresses = [a for r in records for k, a in ndp.map(r) if k == "address"]
    assert {a["address_type"] for a in addresses} == {"physical", "postal"}
    assert {a["country"] for a in addresses} == {"US"}


def test_inactive_stub_service_gets_parent_org(records):
    rec = find(records, "211bayarea-10595170")
    objects = ndp.map(rec)
    stub = next(s for s in by_kind(objects, "service") if s["name"] == "Zzz_ Holiday Programs")
    assert stub["status"] == "inactive"
    assert stub["organization_id"] == hsds_id("ndp", "organization", "211bayarea-10595170")


def test_links_without_a_location_are_skipped(records):
    for rec in records:
        links = by_kind(ndp.map(rec), "service_at_location")
        usable = [s for s in rec["servicesAtLocations"] if s["idService"] and s["idLocation"]]
        assert len(links) == len(usable)


def test_languages_split_and_coded(records):
    languages = [o for r in records for k, o in ndp.map(r) if k == "language"]
    assert {"name": "Spanish", "code": "es"}.items() <= next(l for l in languages if l["name"] == "Spanish").items()
    assert all("," not in l["name"] for l in languages)


def test_phone_types():
    rec = {"id": "x", "name": "O", "phones": [
        {"id": "1", "number": "(415) 555-0100", "type": "fax"},
        {"id": "2", "number": "(415) 555-0101", "type": "tty"},
        {"id": "3", "number": "(415) 555-0102", "type": "main"},
    ]}
    assert [p["type"] for p in by_kind(ndp.map(rec), "phone")] == ["fax", "textphone", "voice"]


def test_taxonomy_levels_become_a_parent_chain(records):
    objects = [o for r in records for o in ndp.map(r)]
    terms = {o["id"]: o for k, o in objects if k == "taxonomy_term"}
    leaf = next(t for t in terms.values() if t.get("code") == "HH-0500.2500-050")
    chain = [leaf["name"]]
    while "parent_id" in leaf:
        leaf = terms[leaf["parent_id"]]
        chain.append(leaf["name"])
    assert chain == [
        "Adult High School Diploma Programs", "Graduation Requirements Programs",
        "Adult Education", "Educational Programs", "Education",
    ]
    assert "code" not in leaf  # levels above the leaf get no guessed code


def test_services_link_to_their_most_specific_terms(records):
    rec = find(records, "211bayarea-10595231")
    objects = ndp.map(rec)
    terms = {o["id"]: o for o in by_kind(objects, "taxonomy_term")}
    for attr in by_kind(objects, "attribute"):
        assert attr["link_entity"] == "service"
        assert attr["taxonomy_term_id"] in terms
    assert [k for k, _ in objects].index("taxonomy") < [k for k, _ in objects].index("taxonomy_term")


def test_target_populations_become_terms(records):
    terms = [o for r in records for k, o in ndp.map(r) if k == "taxonomy_term"]
    older = next(t for t in terms if t.get("code") == "YB-8000")
    assert older["name"] == "Older Adults" and "parent_id" not in older


def test_end_to_end(pages):
    store = Store()
    result = run(ndp, pages, store, "run-1")
    assert result["records"] == 20
    assert result["objects"] == result["kept"] + result["quarantined"]

    quarantined = store.quarantined("run-1")
    reasons = Counter((q["kind"], "quality rule" in q["errors"][0]) for q in quarantined)
    # 4 "Various Locations" addresses (no city / postal code), 3 web addresses posing as phones.
    assert reasons == {("address", False): 4, ("phone", True): 3}

    kept = store.kept()
    assert all(validate(kind, body) == [] for kind, body in kept)
    assert len(store.kept("organization")) == 20 and len(store.kept("taxonomy")) == 1

    # The locations behind the quarantined addresses are still kept.
    address_parents = {q["object_id"] for q in quarantined if q["kind"] == "address"}
    assert address_parents and len(store.kept("location")) == 23

    run(ndp, pages, store, "run-2")
    assert store.kept() == kept  # idempotent


def test_odrs_and_ndp_share_one_store(pages):
    from transform import odrs
    store = Store()
    run(odrs, [json.loads((Path(__file__).parents[1] / "data/samples/ODRS/BayesExport_20250729.json").read_text())], store, "o")
    run(ndp, pages, store, "n")
    sources = Counter(o["x_resource_engine"]["source"]["system"] for _, o in store.kept("service"))
    assert sources == {"odrs": 50, "ndp": 36}
