"""Suspicious-listing detection: each signal, negation handling, scoring, and the
threshold. See maintenance/risk.py and maintenance/suspicious_settings.py."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from maintenance.extractor.extract import find_upfront_fee
from maintenance.extractor.parse import html_to_text
from maintenance.risk import assess, in_registry, names_match, score, suspicious_finding
from maintenance.suspicious_settings import THRESHOLD

PAGES = Path(__file__).parents[2] / "data" / "sample_websites"
TODAY = date(2026, 11, 4)
URL = "https://quickrentrescue.example/apply"


def doc(name="Bay Area Rent Relief Now", fees=None, address=True):
    locations = (
        [{"location": {"addresses": [{"city": "Oakland", "postal_code": "94601"}]}}]
        if address
        else []
    )
    return {
        "id": "00000000-0000-4000-8000-000000000001",
        "name": "Grants",
        "status": "active",
        "organization": {"name": name},
        "service_at_locations": locations,
        "fees": fees,
    }


# --- strong signals -----------------------------------------------------------------------


def test_fee_before_help_signal():
    text = "Pay a one-time $99 processing fee to reserve your grant."
    signals = assess(doc(), text, URL, None, TODAY)
    assert signals[0].code == "fee_before_help"
    assert "$99" in signals[0].quote


def test_fee_negation_phrases_never_flag():
    for text in [
        "There is no processing fee. Grants up to $5,000.",
        "Help is free of charge, up to $500 in assistance.",
        "We never ask for payment of any kind. Up to $5,000 available.",
        "The $99 application fee is waived for qualifying families.",
    ]:
        assert find_upfront_fee(text) is None, text
        assert not any(s.code == "fee_before_help" for s in assess(doc(), text, URL, None, TODAY))


def test_sensitive_data_signal():
    signals = assess(doc(), "To apply, give us your SSN and card number.", URL, None, TODAY)
    assert any(s.code == "sensitive_data_request" for s in signals)


def test_sensitive_data_negation():
    signals = assess(doc(), "We will never ask for your SSN or bank information.", URL, None, TODAY)
    assert not any(s.code == "sensitive_data_request" for s in signals)


# --- medium signals ------------------------------------------------------------------------


def test_urgency_signal():
    signals = assess(doc(), "Guaranteed approval. Act now, limited spots!", URL, None, TODAY)
    assert any(s.code == "urgency_pressure" for s in signals)


def test_fake_official_signal_only_off_a_gov_domain():
    text = "This is the official government assistance program."
    assert any(s.code == "fake_official" for s in assess(doc(), text, URL, None, TODAY))
    assert not any(
        s.code == "fake_official"
        for s in assess(doc(), text, "https://example.gov/aid", None, TODAY)
    )


def test_fee_mismatch_signal_needs_both_a_fee_and_no_fee_on_record():
    text = "Pay a $50 fee to apply."
    assert any(s.code == "fee_mismatch" for s in assess(doc(fees=None), text, URL, None, TODAY))
    assert not any(
        s.code == "fee_mismatch"
        for s in assess(doc(fees=[{"amount": "50"}]), text, URL, None, TODAY)
    )


# --- weak signals --------------------------------------------------------------------------


def test_domain_mismatch_url_shortener():
    signals = assess(doc("Coastside Hope"), "Welcome.", "https://bit.ly/abc123", None, TODAY)
    assert any(s.code == "domain_mismatch" for s in signals)


def test_domain_mismatch_free_hosting():
    signals = assess(
        doc("Coastside Hope"), "Welcome.", "https://coastsidehelp.wixsite.com/home", None, TODAY
    )
    assert any(s.code == "domain_mismatch" for s in signals)


def test_domain_mismatch_unrelated_name():
    signals = assess(
        doc("Coastside Hope"), "Welcome.", "https://totallydifferent.example", None, TODAY
    )
    assert any(s.code == "domain_mismatch" for s in signals)
    signals = assess(
        doc("Coastside Hope"), "Welcome.", "https://coastsidehope.example", None, TODAY
    )
    assert not any(s.code == "domain_mismatch" for s in signals)


def test_new_domain_signal():
    recent = date(2026, 10, 20).isoformat()
    assert any(
        s.code == "new_domain" for s in assess(doc("Quick Rent Rescue"), "x", URL, recent, TODAY)
    )
    old = date(2015, 1, 1).isoformat()
    assert not any(
        s.code == "new_domain" for s in assess(doc("Quick Rent Rescue"), "x", URL, old, TODAY)
    )
    assert not any(
        s.code == "new_domain" for s in assess(doc("Quick Rent Rescue"), "x", URL, None, TODAY)
    )
    assert not any(
        s.code == "new_domain"
        for s in assess(doc("Quick Rent Rescue"), "x", URL, "not a date", TODAY)
    )


def test_registry_lookup():
    assert in_registry("Coastside Hope")
    assert in_registry("COASTSIDE HOPE")
    assert not in_registry("Quick Rent Rescue")


def test_registry_signal_alone_is_never_enough_to_flag():
    signals = assess(doc("Quick Rent Rescue"), "Free food, no strings attached.", URL, None, TODAY)
    assert [s.code for s in signals] == ["not_in_registry"]
    assert score(signals) < THRESHOLD
    assert suspicious_finding(doc()["id"], doc(), URL, signals, None) is None


def test_messaging_only_contact_signal():
    signals = assess(
        doc("Quick Rent Rescue", address=False),
        "Message us at wa.me/15551234567.",
        URL,
        None,
        TODAY,
    )
    assert any(s.code == "messaging_only_contact" for s in signals)


def test_po_box_still_counts_as_a_physical_address():
    doc_with_po_box = doc("Quick Rent Rescue", address=True)
    doc_with_po_box["service_at_locations"] = [
        {"location": {"addresses": [{"address_1": "PO Box 123", "city": "Oakland"}]}}
    ]
    signals = assess(doc_with_po_box, "Message us at wa.me/15551234567.", URL, None, TODAY)
    assert not any(s.code == "messaging_only_contact" for s in signals)


# --- scoring and the threshold --------------------------------------------------------------


def test_names_match_ignores_abbreviations_and_case():
    assert names_match("Stanford Community Law Clinic", "Stanford Community Law Clinic (CLC)")
    assert names_match("Puente de la Costa Sur", "Puente De La Costa Sur")
    assert not names_match("California Housing Relief Center", "Bay Area Rent Relief Now")


def test_fake_site_scores_above_threshold_and_is_flagged():
    html = (PAGES / "quick-rent-rescue.html").read_text()
    text = html_to_text(html)
    signals = assess(doc("Quick Rent Rescue"), text, URL, date(2026, 10, 20).isoformat(), TODAY)
    codes = {s.code for s in signals}
    assert {
        "fee_before_help",
        "sensitive_data_request",
        "urgency_pressure",
        "fake_official",
    } <= codes
    assert score(signals) >= THRESHOLD
    flag = suspicious_finding(doc()["id"], doc(), URL, signals, "offline-rules")
    assert flag is not None and flag.category == "suspicious"
    assert all("scam" not in s["message"].lower() for s in flag.risk_signals)
    assert all("fraud" not in s["message"].lower() for s in flag.risk_signals)
    assert all({"code", "weight", "message", "quote"} <= s.keys() for s in flag.risk_signals)


def test_control_page_stays_unflagged():
    html = (PAGES / "oak-street-legal-control.html").read_text()
    text = html_to_text(html)
    signals = assess(
        doc("Oak Street Legal Clinic"), text, "https://oakstreetlegal.example", None, TODAY
    )
    assert score(signals) < THRESHOLD
    assert (
        suspicious_finding(doc()["id"], doc(), "https://oakstreetlegal.example", signals, None)
        is None
    )


def test_extra_bedrock_reasons_never_change_the_score():
    signals = assess(doc(), "Guaranteed approval! Act now.", URL, None, TODAY)
    before = score(signals)
    flag = suspicious_finding(
        doc()["id"], doc(), URL, signals, None, extra_reasons=["An extra grounded reason"]
    )
    assert score(signals) == before  # the score computed before grounding is unchanged
    if flag:
        assert any(s["message"] == "An extra grounded reason" for s in flag.risk_signals)
