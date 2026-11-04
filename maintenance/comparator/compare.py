"""Compare what a page states with what the record says. Facts, not wording.

The extractor reports page facts with quotes. This module, in plain code:

- drops any fact whose quote is not actually on the page (no quote, no finding),
- normalizes both sides (phone digits, day/time slots, case-folded names, accessible yes/no),
- emits one finding per field that factually differs (``discrepancy``) or that the record
  lacks while the page states it (``missing_information``),
- assigns the check exactly one category.

A page that doesn't mention a fact is never a discrepancy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from contracts.website_source import WebsiteResult
from maintenance.extractor.extract import Extraction, PageFacts, accessibility_class
from maintenance.extractor.parse import DAY_CODES, area_name, phone_digits, quote_on_page

log = logging.getLogger(__name__)

# Statuses/errors that mean the site is gone (actively bad), vs. we just couldn't read it.
GONE_STATUSES = {404, 410}
GONE_ERRORS = ("nxdomain", "domain does not exist", "name or service not known")

FIELD_LABELS = {
    "phone": "Phone",
    "hours": "Hours",
    "languages": "Languages",
    "service_area": "Service area",
    "eligibility": "Eligibility",
    "accessibility": "Accessibility",
    "status": "Status",
    "website": "Website",
    "listing": "Listing",
}


@dataclass
class FindingDraft:
    category: str
    field: str | None
    entity_type: str
    entity_id: str
    entity_field: str
    evidence_url: str
    current_value: Any = None
    proposed_value: Any = None
    evidence_quote: str | None = None
    http_status: int | None = None
    error: str | None = None
    confidence: float = 0.9
    model_id: str | None = None
    risk_signals: list[str] = field(default_factory=list)


@dataclass
class CheckOutcome:
    category: str
    findings: list[FindingDraft]
    dropped_facts: list[str] = field(default_factory=list)


# --- record side ----------------------------------------------------------------------------


def _all_phones(doc: dict) -> list[dict]:
    phones = list(doc.get("phones") or [])
    for sal in doc.get("service_at_locations") or []:
        phones += sal.get("phones") or []
        phones += (sal.get("location") or {}).get("phones") or []
    phones += (doc.get("organization") or {}).get("phones") or []
    return phones


def _slots(schedules: list[dict]) -> list[tuple[str, str, str]]:
    out = []
    for sched in schedules:
        if not (sched.get("byday") and sched.get("opens_at") and sched.get("closes_at")):
            continue
        for day in sched["byday"].split(","):
            out.append((day.strip(), sched["opens_at"][:5], sched["closes_at"][:5]))
    return sorted(set(out), key=lambda s: (DAY_CODES.index(s[0]) if s[0] in DAY_CODES else 9, s[1]))


def _first_location(doc: dict) -> dict | None:
    for sal in doc.get("service_at_locations") or []:
        if sal.get("location"):
            return sal["location"]
    return None


def record_facts(doc: dict) -> dict[str, Any]:
    """The facts a specialist verifies, read from the stored (local-time) nested service."""
    phones = _all_phones(doc)
    hours = _slots(doc.get("schedules") or [])
    if not hours:
        for sal in doc.get("service_at_locations") or []:
            hours = _slots(sal.get("schedules") or []) or _slots(
                (sal.get("location") or {}).get("schedules") or []
            )
            if hours:
                break
    location = _first_location(doc)
    access = [a.get("description") for a in (location or {}).get("accessibility") or []]
    return {
        "phone": phones[0]["number"] if phones else None,
        "phone_id": phones[0]["id"] if phones else None,
        "phone_digits": sorted({d for p in phones if (d := phone_digits(p.get("number", "")))}),
        "hours": [list(slot) for slot in hours],
        "languages": [lang.get("name") for lang in doc.get("languages") or [] if lang.get("name")],
        "service_area": [
            area_name(a["name"]) for a in doc.get("service_areas") or [] if a.get("name")
        ],
        "eligibility": doc.get("eligibility_description"),
        "accessibility": access[0] if access else None,
        "location_id": (location or {}).get("id"),
    }


def hours_to_schedules(slots: list[list[str] | tuple[str, str, str]]) -> list[dict]:
    """[(day, opens, closes)] -> [{byday, opens_at, closes_at}] grouped by time window."""
    groups: dict[tuple[str, str], list[str]] = {}
    for day, opens, closes in slots:
        groups.setdefault((opens, closes), []).append(day)
    return [
        {"byday": ",".join(sorted(days, key=DAY_CODES.index)), "opens_at": o, "closes_at": c}
        for (o, c), days in sorted(groups.items(), key=lambda g: DAY_CODES.index(g[1][0]))
    ]


def _fold(values: list[str]) -> set[str]:
    return {v.strip().casefold() for v in values if v and v.strip()}


# --- comparison -----------------------------------------------------------------------------


def site_failure(service_id: str, url: str, site: WebsiteResult) -> CheckOutcome | None:
    """A site we couldn't read: gone -> actively_bad, otherwise -> check_failed. Never a guess."""
    if site.error is None and site.status is not None and 200 <= site.status < 300 and site.content:
        return None
    reason = site.error or (f"HTTP {site.status}" if site.status else "No content returned")
    gone = site.status in GONE_STATUSES or any(word in reason.lower() for word in GONE_ERRORS)
    category = "actively_bad" if gone else "check_failed"
    draft = FindingDraft(
        category=category,
        field="website",
        entity_type="service",
        entity_id=service_id,
        entity_field="status" if gone else "url",
        evidence_url=url,
        evidence_quote=reason,
        http_status=site.status,
        error=reason,
        confidence=0.8 if gone else 1.0,
    )
    return CheckOutcome(category=category, findings=[draft])


def compare(
    service_id: str, doc: dict, page_text: str, url: str, extraction: Extraction
) -> CheckOutcome:
    record = record_facts(doc)
    facts: PageFacts = extraction.facts
    findings: list[FindingDraft] = []
    dropped: list[str] = []

    def quoted(name: str):
        fact = getattr(facts, name)
        if fact is None:
            return None
        if not quote_on_page(fact.quote, page_text):
            dropped.append(name)
            log.warning("dropped fact: quote not on page", extra={"field": name, "url": url})
            return None
        return fact

    def add(category, field_name, entity_type, entity_id, entity_field, current, proposed, quote):
        findings.append(
            FindingDraft(
                category=category,
                field=field_name,
                entity_type=entity_type,
                entity_id=entity_id or service_id,
                entity_field=entity_field,
                evidence_url=url,
                current_value=current,
                proposed_value=proposed,
                evidence_quote=quote,
                confidence=0.9 if extraction.model_id == "offline-rules" else 0.8,
                model_id=extraction.model_id,
            )
        )

    if (closure := quoted("closure")) and closure.closed:
        add("actively_bad", "status", "service", service_id, "status",
            doc.get("status"), "inactive", closure.quote)  # fmt: skip

    if phone := quoted("phone"):
        digits = phone_digits(phone.number)
        if not record["phone"]:
            add("missing_information", "phone", "service", service_id, "phones",
                None, phone.number, phone.quote)  # fmt: skip
        elif digits and digits not in record["phone_digits"]:
            add("discrepancy", "phone", "phone", record["phone_id"], "number",
                record["phone"], phone.number, phone.quote)  # fmt: skip

    if hours := quoted("hours"):
        page_slots = sorted(
            {(s.day, s.opens, s.closes) for s in hours.slots},
            key=lambda s: (DAY_CODES.index(s[0]), s[1]),
        )
        proposed = hours_to_schedules(page_slots)
        current = hours_to_schedules(record["hours"]) if record["hours"] else None
        if not record["hours"]:
            add("missing_information", "hours", "service", service_id, "schedules",
                None, proposed, hours.quote)  # fmt: skip
        elif {tuple(s) for s in record["hours"]} != set(page_slots):
            add("discrepancy", "hours", "service", service_id, "schedules",
                current, proposed, hours.quote)  # fmt: skip

    for name, entity_field in (("languages", "languages"), ("service_area", "service_areas")):
        if fact := quoted(name):
            page_values = (
                [area_name(v) for v in fact.values]
                if name == "service_area"
                else [v.strip() for v in fact.values]
            )
            have = record[name]
            if not have:
                add("missing_information", name, "service", service_id, entity_field,
                    None, page_values, fact.quote)  # fmt: skip
            elif _fold(page_values) - _fold(have):  # the page states more than the record
                add("discrepancy", name, "service", service_id, entity_field,
                    have, page_values, fact.quote)  # fmt: skip

    if eligibility := quoted("eligibility"):
        if not record["eligibility"]:
            add("missing_information", "eligibility", "service", service_id,
                "eligibility_description", None, eligibility.text, eligibility.quote)  # fmt: skip
        elif eligibility.differs_from_record:
            add("discrepancy", "eligibility", "service", service_id, "eligibility_description",
                record["eligibility"], eligibility.text, eligibility.quote)  # fmt: skip

    if access := quoted("accessibility"):
        page_class = access.wheelchair_accessible
        record_class = accessibility_class(record["accessibility"])
        if page_class is not None:
            if record_class is None and not record["accessibility"]:
                add("missing_information", "accessibility", "location", record["location_id"],
                    "accessibility", None, access.quote, access.quote)  # fmt: skip
            elif record_class is not None and record_class != page_class:
                current = record["accessibility"]
                add("discrepancy", "accessibility", "location", record["location_id"],
                    "accessibility", current, access.quote, access.quote)  # fmt: skip

    categories = {f.category for f in findings}
    for category in ("actively_bad", "discrepancy", "missing_information"):
        if category in categories:
            return CheckOutcome(category=category, findings=findings, dropped_facts=dropped)
    return CheckOutcome(category="verified", findings=[], dropped_facts=dropped)
