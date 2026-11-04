"""Partner spreadsheets: parsing, unpacking, mapping suggestions, and the adapter end to end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingest.adapters.spreadsheet import (
    SpreadsheetAdapter,
    normalize_website,
    phone_object,
    read_csv,
    schedules,
    unpack,
)
from ingest.mapping import bedrock_suggestion, offline_suggestion, suggest_mapping
from ingest.pipeline.run import run_ingest
from ingest.transformer.assemble import served
from shared.hsds_validation import find_issues

SAMPLE = Path(__file__).parents[2] / "data/samples/spreadsheet/county_food_pantries.csv"
SID = "00000000-0000-4000-8000-000000000001"


def sample_mapping():
    headers, rows = read_csv(SAMPLE.read_bytes())
    return {c.column: c.target for c in offline_suggestion(headers, rows).columns}


def test_read_csv_handles_bom_latin1_and_blank_rows():
    headers, rows = read_csv("﻿Name,City\nA,Oakland\n,\n".encode())
    assert headers == ["Name", "City"] and rows == [{"Name": "A", "City": "Oakland"}]
    _, rows = read_csv("Name\nCafé\n".encode("latin-1"))
    assert rows[0]["Name"] == "Café"
    with pytest.raises(ValueError, match="same name"):
        read_csv(b"Name,Name\na,b\n")


def test_unpack_packed_column_keeps_unknown_keys_as_notes():
    fields, notes = unpack("Hours: Mon 9-5 | Who can come: Anyone | Note: bring a bag | junk")
    assert fields == {"hours": "Mon 9-5", "eligibility": "Anyone"}
    assert notes == ["Note: bring a bag", "junk"]


@pytest.mark.parametrize(
    "raw, number, ext",
    [
        ("(707) 555-0101", "(707) 555-0101", None),
        ("707.555.0102", "(707) 555-0102", None),
        ("7075550103", "(707) 555-0103", None),
        ("707-555-0104 ext 2", "(707) 555-0104", 2),
        ("+1 707 555 0105", "(707) 555-0105", None),
        ("call the church office", "call the church office", None),
    ],
)
def test_phone_formats(raw, number, ext):
    phone = phone_object(raw, SID, SID)
    assert phone["number"] == number and phone.get("extension") == ext


def test_monthly_hours_are_kept_as_text_not_guessed():
    [(_, text)] = schedules("1st and 3rd Friday 3pm-5pm", "k", SID, "partner_x")
    assert text["description"] == "1st and 3rd Friday 3pm-5pm" and "byday" not in text
    [(_, weekly)] = schedules("Tue & Thu 2pm-6pm", "k", SID, "partner_x")
    assert (weekly["byday"], weekly["opens_at"], weekly["closes_at"]) == ("TU,TH", "14:00", "18:00")


def test_website_normalized():
    assert normalize_website("fairfieldpantry.example") == "https://fairfieldpantry.example"
    assert normalize_website("http://x.example/") == "http://x.example/"


def test_offline_suggestion_maps_every_sample_column():
    assert sample_mapping() == {
        "Pantry Name": "service_name", "Run By": "organization_name", "Street": "address_1",
        "City": "city", "Zip": "postal_code", "Phone #": "phone", "Info": "packed",
        "Web": "website",
    }  # fmt: skip


def test_offline_suggestion_uses_values_when_headers_are_unhelpful():
    headers = ["Col A", "Col B", "Col C"]
    rows = [{"Col A": "a@x.example", "Col B": "(415) 555-0100", "Col C": "?"}] * 3
    columns = offline_suggestion(headers, rows).columns
    assert [c.target for c in columns] == ["email", "phone", "ignore"]
    assert columns[2].confidence < 0.5 and "check this column" in columns[2].reason


class StubBedrock:
    def __init__(self, reply):
        self.reply = reply

    def converse(self, **kwargs):
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"output": {"message": {"content": [{"text": self.reply}]}},
                "usage": {"inputTokens": 900, "outputTokens": 200}}  # fmt: skip


def test_bedrock_suggestion_is_validated():
    headers, rows = ["Name", "Tel"], [{"Name": "A", "Tel": "1"}]
    good = json.dumps({"columns": [
        {"column": "Name", "target": "service_name", "confidence": 0.9, "reason": "name"},
        {"column": "Tel", "target": "phone", "confidence": 0.8, "reason": "tel"},
    ]})  # fmt: skip
    result = bedrock_suggestion(headers, rows, StubBedrock(good), "m", 3, 15)
    assert [c.target for c in result.columns] == ["service_name", "phone"]
    assert result.cost_usd == pytest.approx((900 * 3 + 200 * 15) / 1e6)
    with pytest.raises(ValueError):
        bedrock_suggestion(headers, rows, StubBedrock(good.replace("phone", "fax_machine")), "m")


def test_invalid_or_unreachable_bedrock_falls_back_to_offline_rules(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.test")
    headers, rows = read_csv(SAMPLE.read_bytes())
    bad = suggest_mapping(headers, rows, client_factory=lambda: StubBedrock("not json"))
    assert bad.model_id == "offline-rules" and "wasn't usable" in bad.note
    down = suggest_mapping(headers, rows, client_factory=lambda: StubBedrock(RuntimeError("x")))
    assert down.model_id == "offline-rules" and "wasn't reachable" in down.note


def test_sample_spreadsheet_end_to_end(store, raw_store):
    adapter = SpreadsheetAdapter(
        "partner_solano", SAMPLE.name, SAMPLE.read_bytes(), sample_mapping()
    )
    report = run_ingest(adapter, store, raw_store)
    assert (report.received, report.stored, report.quarantined) == (7, 7, 0)
    reasons = {(q["native_id"].split("/")[0], q["kind"]) for q in report.quarantine_reasons}
    assert reasons == {
        ("vacaville-mobile-pantry", "address"),
        ("rio-vista-churches-together", "phone"),
    }
    assert raw_store.read(report.raw_refs[0]) == SAMPLE.read_bytes()  # raw kept as uploaded

    page = store.list_services(__import__("contracts.store").store.ServiceFilter(), 1, 20)
    assert page.total_items == 7
    assert all(find_issues(record, "service") == [] for record in page.contents)
    vallejo = next(r for r in page.contents if r["name"].startswith("Vallejo"))
    assert vallejo["eligibility_description"] == "Solano County residents"
    assert [lang["name"] for lang in vallejo["languages"]] == ["English", "Spanish"]

    again = run_ingest(adapter, store, raw_store)  # same file again: updated in place
    assert again.services_changed == 0
    assert (
        store.list_services(__import__("contracts.store").store.ServiceFilter(), 1, 20).total_items
        == 7
    )


def test_row_without_any_name_is_quarantined(store, raw_store):
    content = b"Pantry Name,City\n,Oakland\nGood Pantry,Oakland\n"
    adapter = SpreadsheetAdapter("partner_t", "t.csv", content, {"Pantry Name": "service_name",
                                                                  "City": "city"})  # fmt: skip
    report = run_ingest(adapter, store, raw_store)
    assert (report.received, report.stored, report.quarantined) == (2, 1, 1)


def test_unknown_target_is_rejected():
    with pytest.raises(ValueError, match="unknown target"):
        SpreadsheetAdapter("p", "f.csv", b"a\n1\n", {"a": "favourite_colour"})


def test_served_hours_have_offsets(store, raw_store):
    adapter = SpreadsheetAdapter(
        "partner_solano", SAMPLE.name, SAMPLE.read_bytes(), sample_mapping()
    )
    run_ingest(adapter, store, raw_store)
    with store.session() as session:
        from sqlalchemy import select

        from shared.models import Service

        doc = (
            session.scalars(select(Service).where(Service.name.like("Fairfield%"))).first().document
        )
    assert served(doc)["schedules"][0]["opens_at"].startswith("14:00-0")
