"""Pipeline behaviour beyond the mappers: raw store, served-form validation, registry."""

from __future__ import annotations

import json

import pytest

from ingest.adapters import odrs
from ingest.adapters.base import ModuleAdapter, RawPage
from ingest.adapters.registry import UnknownSource, get_adapter, source_names
from ingest.pipeline.run import run_ingest, validate_record
from ingest.transformer.assemble import MemoryIndex, assemble_service, served
from shared.hsds_validation import find_issues

RECORD = {
    "id": 1,
    "title": "Free groceries",
    "organization": {"id": 2, "name": "Food Org"},
    "schedule": {"monday_start": "09:00", "monday_end": "17:00"},
    "phones": [{"id": 3, "digits": "415-555-0100"}],
}


def adapter_for(pages: list[bytes]) -> ModuleAdapter:
    adapter = ModuleAdapter(odrs, inputs=[])
    adapter.fetch_pages = lambda since=None: iter(
        [RawPage(f"p{i}.json", content) for i, content in enumerate(pages)]
    )
    return adapter


def test_raw_payload_is_saved_byte_for_byte(store, raw_store):
    content = b'{"opportunities": [' + json.dumps(RECORD).encode() + b"]}  \n"
    report = run_ingest(adapter_for([content]), store, raw_store)
    assert raw_store.read(report.raw_refs[0]) == content


def test_counts_reconcile_and_run_is_recorded(store, raw_store):
    page = json.dumps({"opportunities": [RECORD, {"id": 9}]}).encode()
    report = run_ingest(adapter_for([page]), store, raw_store, triggered_by="tester")
    assert report.received == report.stored + report.quarantined == 2
    [run] = store.list_runs()
    assert (run.status, run.received, run.stored, run.quarantined) == ("succeeded", 2, 1, 1)
    assert run.triggered_by == "tester"


def test_invalid_json_page_fails_the_run_loudly(store, raw_store):
    with pytest.raises(ValueError, match="not valid JSON"):
        run_ingest(adapter_for([b"{not json"]), store, raw_store)
    [run] = store.list_runs()
    assert run.status == "failed" and "not valid JSON" in run.error


def test_stored_schedule_is_local_and_served_schedule_has_offset(store, raw_store):
    from datetime import date

    run_ingest(adapter_for([json.dumps({"opportunities": [RECORD]}).encode()]), store, raw_store)
    page = store.list_services(__import__("contracts.store").store.ServiceFilter(), 1, 10)
    [svc] = page.contents
    with store.session() as session:
        doc = store.assemble(session, svc["id"])
    assert doc["schedules"][0]["opens_at"] == "09:00"
    assert doc["schedules"][0]["x_resource_engine"]["timezone"] == "America/Los_Angeles"
    assert served(doc, on=date(2026, 7, 1))["schedules"][0]["opens_at"] == "09:00-07:00"
    assert served(doc, on=date(2026, 12, 1))["schedules"][0]["opens_at"] == "09:00-08:00"
    assert find_issues(svc, "service") == []


def test_service_with_unservable_hours_is_quarantined_with_children():
    record = {**RECORD, "schedule": {"monday_start": "9am", "monday_end": "5pm"}}
    objects = odrs.map(record)
    good, bad, docs = validate_record(objects)
    kinds = {k for k, _, _ in bad}
    assert {"service", "phone", "schedule"} <= kinds
    assert not docs
    service_reasons = next(r for k, _, r in bad if k == "service")
    assert any("served HSDS service" in r for r in service_reasons)
    assert {k for k, _ in good} == {"organization"}


def test_assembled_service_nests_children():
    objects = odrs.map(RECORD)
    [service] = [o for k, o in objects if k == "service"]
    doc = assemble_service(MemoryIndex(objects), service)
    assert doc["organization"]["name"] == "Food Org"
    assert doc["phones"][0]["number"] == "415-555-0100"


def test_registry_reads_config():
    assert {"odrs", "ndp"} <= set(source_names())
    assert get_adapter("odrs").name == "odrs"
    with pytest.raises(UnknownSource):
        get_adapter("nope")


def test_adapter_satisfies_contract():
    from contracts.adapter import SourceAdapter

    adapter = get_adapter("odrs")
    assert isinstance(adapter, SourceAdapter)
    first = next(adapter.fetch())
    assert adapter.native_id(first) == str(first["id"])
    grouped = adapter.map(first)
    assert grouped["service"][0]["name"] == first["title"]
