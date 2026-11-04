"""Comparator, harm ranking, trust, apply, and the end-to-end evaluation on the sample sites."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from contracts.website_source import WebsiteResult
from ingest.transformer.assemble import served
from maintenance.apply import ApplyError, add_metadata, apply_change
from maintenance.comparator.compare import compare, record_facts, site_failure
from maintenance.extractor.extract import (
    Extraction,
    ListFact,
    PageFacts,
    PhoneFact,
)
from maintenance.extractor.parse import html_to_text
from maintenance.priority import harm_rank
from maintenance.trust import trust_for
from maintenance.website_source.sample import SampleWebsiteSource
from maintenance.worker.run_check import run_checks
from shared.hsds_validation import find_issues
from shared.models import Service

SID = "00000000-0000-4000-8000-000000000001"
DOC = {
    "id": SID,
    "name": "Pantry",
    "status": "active",
    "phones": [{"id": "00000000-0000-4000-8000-000000000002", "number": "(415) 555-0100"}],
    "languages": [{"id": "00000000-0000-4000-8000-000000000003", "name": "English"}],
    "schedules": [
        {"id": "00000000-0000-4000-8000-000000000004", "byday": "MO,TU", "opens_at": "09:00",
         "closes_at": "17:00", "x_resource_engine": {"timezone": "America/Los_Angeles"}},
    ],
}  # fmt: skip


def extraction(**facts) -> Extraction:
    return Extraction(facts=PageFacts(**facts), model_id="offline-rules")


def test_same_fact_different_wording_is_not_flagged():
    page = "Phone: 415.555.0100. We speak english."
    outcome = compare(
        SID, DOC, page, "https://x.example",
        extraction(phone=PhoneFact(number="(415) 555-0100", quote="415.555.0100"),
                   languages=ListFact(values=["english"], quote="english")),
    )  # fmt: skip
    assert outcome.category == "verified" and outcome.findings == []


def test_fact_without_its_quote_on_the_page_is_dropped():
    outcome = compare(
        SID, DOC, "Nothing useful here.", "https://x.example",
        extraction(phone=PhoneFact(number="(415) 555-9999", quote="Call (415) 555-9999")),
    )  # fmt: skip
    assert outcome.findings == [] and outcome.dropped_facts == ["phone"]


def test_page_silent_on_a_fact_is_not_a_discrepancy():
    outcome = compare(SID, DOC, "Welcome!", "https://x.example", extraction())
    assert outcome.category == "verified"


def test_changed_phone_is_one_discrepancy_with_evidence():
    page = "Call (415) 555-0199 today."
    outcome = compare(
        SID, DOC, page, "https://x.example",
        extraction(phone=PhoneFact(number="(415) 555-0199", quote="Call (415) 555-0199 today.")),
    )  # fmt: skip
    [finding] = outcome.findings
    assert (finding.category, finding.field) == ("discrepancy", "phone")
    assert (finding.current_value, finding.proposed_value) == ("(415) 555-0100", "(415) 555-0199")
    assert finding.entity_type == "phone" and finding.evidence_quote in page


@pytest.mark.parametrize(
    "site, category",
    [
        (WebsiteResult(None, None, "DNS lookup failed: NXDOMAIN"), "actively_bad"),
        (WebsiteResult(None, 404, None), "actively_bad"),
        (WebsiteResult(None, 403, "blocked"), "check_failed"),
        (WebsiteResult(None, None, "Timeout after 10s"), "check_failed"),
        (WebsiteResult("", 200, None), "check_failed"),
    ],
)
def test_unreadable_sites_never_guess(site, category):
    outcome = site_failure(SID, "https://x.example", site)
    assert outcome.category == category
    assert outcome.findings[0].proposed_value is None and outcome.findings[0].evidence_quote


def test_harm_ranking_order():
    ordered = sorted(
        [("check_failed", None), ("missing_information", "languages"),
         ("discrepancy", "eligibility"), ("discrepancy", "phone"), ("actively_bad", None),
         ("suspicious", None)],
        key=lambda c: harm_rank(*c),
    )  # fmt: skip
    assert ordered == [
        ("actively_bad", None), ("suspicious", None), ("discrepancy", "phone"),
        ("discrepancy", "eligibility"), ("missing_information", "languages"),
        ("check_failed", None),
    ]  # fmt: skip
    assert harm_rank("discrepancy", "hours") == harm_rank("discrepancy", "phone")


def test_trust_score_and_badge():
    now = datetime(2026, 11, 4, tzinfo=UTC)
    assert trust_for(None, None, []).badge == "Not checked yet"
    fresh = trust_for("verified", now - timedelta(days=2), [], now)
    assert fresh.score == 100 and fresh.level == "high" and fresh.badge == "Verified 2 days ago"
    stale = trust_for("verified", now - timedelta(days=60), [], now)
    assert stale.score < fresh.score
    flagged = trust_for("discrepancy", now, [2], now)
    assert flagged.score == 65 and flagged.badge.startswith("1 open issue")
    assert trust_for("actively_bad", now, [0], now).level == "low"
    minor = trust_for("missing_information", now, [4], now)
    assert minor.score == 85 and minor.level == "medium"  # open issue: never "high"


def test_apply_phone_hours_languages_status_keep_hsds_valid():
    doc = apply_change(DOC, "phone", "(415) 555-0199", current="(415) 555-0100")
    assert doc["phones"][0]["number"] == "(415) 555-0199"
    doc = apply_change(doc, "hours", [{"byday": "FR", "opens_at": "12:00", "closes_at": "14:30"}])
    assert [(s["byday"], s["opens_at"]) for s in doc["schedules"]] == [("FR", "12:00")]
    doc = apply_change(doc, "languages", ["English", "Spanish"])
    assert [lang.get("code") for lang in doc["languages"]] == ["en", "es"]
    doc = apply_change(doc, "status", "inactive")
    doc = add_metadata(doc, "status", "active", "inactive", "sam")
    assert doc["metadata"][-1]["updated_by"] == "sam"
    assert find_issues(served(doc), "service") == []
    assert DOC["phones"][0]["number"] == "(415) 555-0100"  # input not mutated


@pytest.mark.parametrize(
    "field, value",
    [("phone", "call us"), ("hours", []), ("status", "open"), ("languages", [""]), ("nope", 1)],
)
def test_apply_rejects_bad_values(field, value):
    with pytest.raises(ApplyError):
        apply_change(DOC, field, value)


def test_record_facts_reads_nested_service():
    facts = record_facts(DOC)
    assert facts["phone_digits"] == ["4155550100"]
    assert facts["hours"] == [["MO", "09:00", "17:00"], ["TU", "09:00", "17:00"]]


def test_evaluation_every_planted_problem_is_caught_and_nothing_else(loaded_store):
    """Known truth: data/sample_websites/manifest.json. Catch rate 100%, false flags 0."""
    source = SampleWebsiteSource()
    report = run_checks(loaded_store, source)
    assert report.checked == len(source.sites) and report.findings_rejected == 0
    with loaded_store.session() as session:
        from sqlalchemy import select

        from shared.models import Check, Finding

        for site in source.sites:
            [check] = session.scalars(select(Check).where(Check.service_id == site.service_id))
            assert check.category == site.expected["category"], site.planted
            fields = {
                f.field
                for f in session.scalars(
                    select(Finding).where(Finding.service_id == site.service_id)
                )
            }
            if "field" in site.expected:
                assert fields == {site.expected["field"]}, site.planted
            if site.expected["category"] == "verified":
                assert fields == set(), site.planted
            assert sorted(check.risk_signals) == sorted(site.expected.get("signals", [])), (
                site.planted
            )


def test_quotes_stored_verbatim_from_page(loaded_store):
    run_checks(loaded_store)
    source = SampleWebsiteSource()
    from sqlalchemy import select

    from shared.models import Finding

    with loaded_store.session() as session:
        for finding in session.scalars(select(Finding).where(Finding.model_id.is_not(None))):
            site = next(s for s in source.sites if s.service_id == finding.service_id)
            page = html_to_text((source.directory / site.page).read_text())
            assert finding.evidence_quote in page


def test_rerun_does_not_duplicate_findings(loaded_store):
    first = run_checks(loaded_store)
    second = run_checks(loaded_store)
    assert first.findings_created == 7
    assert second.findings_created == 0 and second.findings_refreshed == 7


def test_missing_service_is_reported_not_crashed(loaded_store):
    report = run_checks(loaded_store, service_ids=["00000000-0000-0000-0000-00000000dead"])
    assert report.checked == 0 and report.not_found


def test_service_document_unchanged_by_checks(loaded_store):
    sid = "d02029d4-b4d2-5c9d-8c59-947b76dff913"
    with loaded_store.session() as session:
        before = session.get(Service, sid).document
    run_checks(loaded_store)
    with loaded_store.session() as session:
        assert session.get(Service, sid).document == before  # AI proposes, people apply
