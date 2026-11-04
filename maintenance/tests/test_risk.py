"""Scam risk signals: each signal, the two-signal threshold, and plain wording."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from maintenance.extractor.extract import (
    FeeFact,
    OfflineExtractor,
    PageFacts,
    TextFact,
    find_upfront_fee,
    page_organization_name,
)
from maintenance.extractor.parse import html_to_text
from maintenance.risk import assess, in_registry, names_match, suspicious_finding

PAGES = Path(__file__).parents[2] / "data" / "sample_websites"
TODAY = date(2026, 11, 4)


def doc(name="Bay Area Rent Relief Now"):
    return {"id": "00000000-0000-4000-8000-000000000001", "name": "Grants", "status": "active",
            "organization": {"name": name}}  # fmt: skip


def test_upfront_fee_detection():
    assert (
        find_upfront_fee("Pay a one-time $99 processing fee to reserve your grant.").amount == "$99"
    )
    assert find_upfront_fee("There is no fee. Grants up to $5,000.") is None
    assert find_upfront_fee("We never charge a fee for help with your $20 bill.") is None
    assert find_upfront_fee("Grants up to $5,000 for back rent.") is None  # money but no fee


def test_page_name_comes_from_the_header_or_title():
    page = (PAGES / "fake_rent_relief.html").read_text()
    assert page_organization_name(page) == "California Housing Relief Center"
    assert (
        page_organization_name("<title>Acme Aid – Home</title><p>" + "x" * 100 + "</p>")
        == "Acme Aid"
    )


def test_name_matching_ignores_abbreviations_and_case():
    assert names_match("Stanford Community Law Clinic", "Stanford Community Law Clinic (CLC)")
    assert names_match("Puente de la Costa Sur", "Puente De La Costa Sur")
    assert not names_match("California Housing Relief Center", "Bay Area Rent Relief Now")


def test_registry_lookup():
    assert in_registry("Coastside Hope")
    assert in_registry("COASTSIDE HOPE")
    assert not in_registry("Bay Area Rent Relief Now")


def test_fake_site_has_all_four_signals_and_is_flagged_for_review():
    html = (PAGES / "fake_rent_relief.html").read_text()
    facts = OfflineExtractor().extract(html, {}).facts
    signals = assess(doc(), facts, html_to_text(html), "2026-10-20", TODAY)
    assert [s.code for s in signals] == ["fee_before_help", "new_domain", "name_mismatch",
                                         "not_in_registry"]  # fmt: skip
    flag = suspicious_finding(doc()["id"], doc(), "https://x.example", signals, "offline-rules")
    assert flag.category == "suspicious" and "$99" in flag.evidence_quote
    assert all("scam" not in message.lower() for message in flag.risk_signals)


def test_one_signal_is_not_enough():
    signals = assess(doc("Half Moon Bay Community Fridge"), PageFacts(), "Free food.", None, TODAY)
    assert [s.code for s in signals] == ["not_in_registry"]
    assert suspicious_finding("id", doc(), "https://x.example", signals, None) is None


def test_old_or_unknown_domain_is_not_a_signal():
    facts = PageFacts()
    assert assess(doc("Coastside Hope"), facts, "", "2015-01-01", TODAY) == []
    assert assess(doc("Coastside Hope"), facts, "", None, TODAY) == []
    assert assess(doc("Coastside Hope"), facts, "", "not a date", TODAY) == []


def test_signal_quotes_must_be_on_the_page():
    facts = PageFacts(
        fee_before_help=FeeFact(required=True, amount="$99", quote="Pay $99 now"),
        organization_name=TextFact(text="Other Name", quote="Other Name"),
    )
    assert assess(doc("Coastside Hope"), facts, "Welcome to Coastside Hope", None, TODAY) == []
