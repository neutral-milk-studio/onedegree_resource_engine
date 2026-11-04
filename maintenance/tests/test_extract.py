"""Parsers, the offline extractor, and the Bedrock extractor (stubbed Converse client)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maintenance.extractor.extract import (
    BedrockExtractor,
    ExtractionError,
    MemoryCache,
    OfflineExtractor,
    accessibility_class,
    get_extractor,
    parse_model_json,
)
from maintenance.extractor.parse import (
    find_phone,
    html_to_text,
    parse_days,
    parse_hours_line,
    parse_time,
    quote_on_page,
    split_list,
)

PAGES = Path(__file__).parents[2] / "data" / "sample_websites"
WEEKDAYS = ["MO", "TU", "WE", "TH", "FR"]


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Monday – Friday: 8:30 AM – 5:00 PM", [(d, "08:30", "17:00") for d in WEEKDAYS]),
        ("Fridays: 12:00 PM – 2:30 PM", [("FR", "12:00", "14:30")]),
        ("Tuesday and Thursday: 12 - 4pm", [("TU", "12:00", "16:00"), ("TH", "12:00", "16:00")]),
        ("Sat 9am to noon", [("SA", "09:00", "12:00")]),
        ("Call us any time", []),
    ],
)  # fmt: skip
def test_parse_hours_line(line, expected):
    assert parse_hours_line(line) == expected


def test_parse_days_and_times():
    assert parse_days("Daily") == ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    assert parse_days("Fri - Mon") == ["MO", "FR", "SA", "SU"]
    assert parse_time("12:30 AM") == "00:30" and parse_time("noon") == "12:00"
    assert parse_time("25:00") is None


def test_phone_and_lists():
    assert find_phone("Call 650.276.4101 x 4") == "(650) 276-4101"
    assert find_phone("no number") is None
    assert split_list("English, Spanish and Cantonese.") == ["English", "Spanish", "Cantonese"]


def test_quotes_are_checked_against_the_page_verbatim_modulo_whitespace():
    text = html_to_text("<p>Fridays:  12:00 PM – 2:30 PM</p>")
    assert quote_on_page("Fridays: 12:00 PM – 2:30 PM", text)
    assert quote_on_page("Fridays: 12:00 PM - 2:30 PM", text)  # dash style isn't a fact
    assert not quote_on_page("Saturdays: 12:00 PM", text)
    assert not quote_on_page("", text)


def test_accessibility_meaning_not_wording():
    assert accessibility_class("Fully accessible to individuals using mobility aids.") is True
    assert accessibility_class("Wheelchair accessible entrance on Amador Street.") is True
    assert accessibility_class("The building is not wheelchair accessible.") is False
    assert accessibility_class("Not Applicable.") is None


def test_offline_extractor_reads_sample_page():
    facts = OfflineExtractor().extract((PAGES / "coastside_hope_rent.html").read_text(), {}).facts
    assert facts.phone.number == "(650) 726-9071"
    assert {(s.day, s.opens, s.closes) for s in facts.hours.slots} >= {("WE", "13:00", "17:00")}
    assert facts.languages.values == ["English", "Spanish"]
    assert facts.accessibility.wheelchair_accessible is True
    assert facts.closure is None


def test_offline_extractor_finds_closure_notice():
    page = "<h2>Update</h2><p>Our pantry has closed permanently. Thank you.</p>"
    facts = OfflineExtractor().extract(page.replace("closed permanently", "permanently closed"), {})
    assert facts.facts.closure.closed and "permanently closed" in facts.facts.closure.quote


class StubBedrock:
    def __init__(self, reply: str | Exception, usage=(1200, 150)) -> None:
        self.reply, self.usage, self.calls = reply, usage, []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return {
            "output": {"message": {"content": [{"text": self.reply}]}},
            "usage": {"inputTokens": self.usage[0], "outputTokens": self.usage[1]},
        }


REPLY = json.dumps(
    {
        "phone": {"number": "(650) 879-1700", "quote": "Our new main line is (650) 879-1700."},
        "languages": {"values": ["Spanish", "English"], "quote": "Spanish and English"},
    }
)


def test_bedrock_extractor_parses_costs_and_caches():
    client = StubBedrock(REPLY)
    extractor = BedrockExtractor(
        client,
        "anthropic.test-model",
        MemoryCache(),
        input_price_per_mtok=3,
        output_price_per_mtok=15,
    )
    page = (PAGES / "puente_financial_help.html").read_text()
    first = extractor.extract(page, {"eligibility": "Residents"})
    assert first.facts.phone.number == "(650) 879-1700" and not first.cached
    assert first.cost_usd == pytest.approx((1200 * 3 + 150 * 15) / 1_000_000)
    call = client.calls[0]
    assert call["modelId"] == "anthropic.test-model"
    assert call["inferenceConfig"]["temperature"] == 0
    assert "Our new main line" in call["messages"][0]["content"][0]["text"]

    second = extractor.extract(page, {"eligibility": "Residents"})
    assert second.cached and second.cost_usd == 0 and len(client.calls) == 1  # same page: no call


def test_bedrock_reply_must_be_strict_json():
    assert parse_model_json("```json\n" + REPLY + "\n```").phone.number == "(650) 879-1700"
    with pytest.raises(ExtractionError):
        parse_model_json("Sure! The phone is (650) 879-1700.")
    with pytest.raises(ExtractionError):
        parse_model_json(json.dumps({"hours": {"slots": [{"day": "Monday"}], "quote": "x"}}))


def test_bedrock_failure_is_an_extraction_error():
    extractor = BedrockExtractor(StubBedrock(RuntimeError("throttled")), "m", MemoryCache(), 1, 1)
    with pytest.raises(ExtractionError, match="throttled"):
        extractor.extract("<p>hi</p>", {})


def test_get_extractor_uses_bedrock_only_when_configured(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    assert isinstance(get_extractor(), OfflineExtractor)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.test-model")
    extractor = get_extractor(client_factory=lambda: StubBedrock(REPLY))
    assert isinstance(extractor, BedrockExtractor) and extractor.model_id == "anthropic.test-model"
