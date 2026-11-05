"""Maintenance worker: website -> extract (Bedrock or offline) -> compare -> findings.

For each record: load its website from the WebsiteSource, extract the facts the page
states, compare them with the record in code, and store one check (one category) plus one
finding per factual difference. A site that can't be read is a ``check_failed`` (or
``actively_bad`` when it's gone) finding, never a silent skip and never a guess.

Usage::

    python -m maintenance.worker.run_check            # every record with a sample page
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

from api.app.store_pg import PgHsdsStore
from contracts.findings import FindingRejected
from maintenance.comparator.compare import (
    CheckOutcome,
    FindingDraft,
    compare,
    record_facts,
    site_failure,
)
from maintenance.extractor.extract import (
    Extraction,
    ExtractionError,
    OfflineExtractor,
    get_extractor,
)
from maintenance.extractor.parse import html_to_text
from maintenance.findings_store.pg_findings import PgCache, PgFindingsStore
from maintenance.risk import assess, suspicious_finding
from maintenance.website_source import get_website_source
from shared.models import Check, Service

log = logging.getLogger(__name__)


@dataclass
class MaintenanceReport:
    checked: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    findings_created: int = 0
    findings_refreshed: int = 0
    findings_rejected: int = 0
    model_calls: int = 0
    cached: int = 0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""
    elapsed_ms: int = 0
    not_found: list[str] = field(default_factory=list)


def website_url(source, service_id: str, doc: dict) -> str | None:
    """The page to check: the sample stand-in when one is configured, else the record's own."""
    if hasattr(source, "url_for"):
        return source.url_for(service_id)
    return (doc.get("organization") or {}).get("website") or doc.get("url")


def check_one(service_id: str, doc: dict, source, extractor) -> tuple[CheckOutcome, dict]:
    """Run one check. Returns the outcome and check metadata (url, page hash, usage)."""
    meta = {"url": None, "page_hash": None, "http_status": None, "error": None,
            "extraction": None, "risk_signals": []}  # fmt: skip
    url = website_url(source, service_id, doc)
    meta["url"] = url
    if not url:
        draft = FindingDraft(
            category="check_failed", field="website", entity_type="service",
            entity_id=service_id, entity_field="url", evidence_url="about:blank",
            evidence_quote="The record has no website to check.", error="no website",
            confidence=1.0,
        )  # fmt: skip
        return CheckOutcome("check_failed", [draft]), meta

    site = source.get(url)
    meta["http_status"], meta["error"] = site.status, site.error
    if (failed := site_failure(service_id, url, site)) is not None:
        return failed, meta

    page_text = html_to_text(site.content or "")
    meta["page_hash"] = hashlib.sha256(page_text.encode()).hexdigest()
    try:
        extraction: Extraction = extractor.extract(site.content or "", record_facts(doc))
    except ExtractionError as exc:
        meta["error"] = str(exc)
        draft = FindingDraft(
            category="check_failed", field="website", entity_type="service",
            entity_id=service_id, entity_field="url", evidence_url=url,
            evidence_quote=f"Couldn't read the page's facts: {exc}"[:500], error=str(exc),
            confidence=1.0, model_id=getattr(extractor, "model_id", None),
        )  # fmt: skip
        return CheckOutcome("check_failed", [draft]), meta
    meta["extraction"] = extraction
    outcome = compare(service_id, doc, page_text, url, extraction)

    # Scam risk signals, in the same check. Two or more -> flagged for review.
    registered = source.domain_registered(url) if hasattr(source, "domain_registered") else None
    signals = assess(doc, extraction.facts, page_text, registered)
    meta["risk_signals"] = [s.code for s in signals]
    if flag := suspicious_finding(service_id, doc, url, signals, extraction.model_id):
        outcome.findings.insert(0, flag)
        if outcome.category != "actively_bad":
            outcome.category = "suspicious"
    return outcome, meta


def run_checks(
    store: PgHsdsStore,
    website_source=None,
    extractor=None,
    service_ids: list[str] | None = None,
    actor: str = "maintenance",
) -> MaintenanceReport:
    """Check ``service_ids`` (default: every record the website source has a page for)."""
    started = time.monotonic()
    source = website_source or get_website_source(store)
    if extractor is None:
        extractor = get_extractor(cache=PgCache(store.session))
    report = MaintenanceReport(model_id=getattr(extractor, "model_id", ""))
    ids = service_ids or (source.service_ids() if hasattr(source, "service_ids") else [])
    categories: Counter[str] = Counter()

    for service_id in ids:
        with store.session() as session:
            row = session.get(Service, service_id)
            if row is None:
                report.not_found.append(service_id)
                log.warning("check skipped: no such service", extra={"service_id": service_id})
                continue
            outcome, meta = check_one(service_id, row.document, source, extractor)
            extraction: Extraction | None = meta["extraction"]
            check = Check(
                service_id=service_id,
                url=meta["url"],
                category=outcome.category,
                http_status=meta["http_status"],
                error=meta["error"],
                page_hash=meta["page_hash"],
                model_id=extraction.model_id if extraction else None,
                input_tokens=extraction.input_tokens if extraction else 0,
                output_tokens=extraction.output_tokens if extraction else 0,
                cost_usd=extraction.cost_usd if extraction else 0.0,
                cached=bool(extraction and extraction.cached),
                risk_signals=meta["risk_signals"],
            )
            session.add(check)
            session.flush()
            findings = PgFindingsStore(session)
            for draft in outcome.findings:
                try:
                    _, created = findings.save_draft(check.id, service_id, draft)
                except FindingRejected as exc:  # logged and counted, never stored evidence-free
                    report.findings_rejected += 1
                    log.warning("finding rejected", extra={"service_id": service_id,
                                                           "reason": str(exc)})  # fmt: skip
                    continue
                check.findings_created += created
                report.findings_created += created
                report.findings_refreshed += not created

        report.checked += 1
        categories[outcome.category] += 1
        if extraction:
            if extraction.cached:
                report.cached += 1
            elif not isinstance(extractor, OfflineExtractor):
                report.model_calls += 1
            report.cost_usd += extraction.cost_usd
            report.input_tokens += extraction.input_tokens
            report.output_tokens += extraction.output_tokens
        log.info("checked", extra={"service_id": service_id, "category": outcome.category,
                                   "findings": len(outcome.findings),
                                   "dropped_facts": outcome.dropped_facts})  # fmt: skip

    report.by_category = dict(categories)
    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    with store.session() as session:
        summary = ", ".join(f"{n} {c.replace('_', ' ')}" for c, n in sorted(categories.items()))
        store.audit(
            session,
            actor=actor,
            action="maintenance_run",
            change_source="ai",
            entity_type="maintenance",
            entity_id=report.model_id or "unknown",
            new_value={k: v for k, v in asdict(report).items() if k != "not_found"},
            summary=f"Checked {report.checked} websites: {summary or 'nothing to check'}",
        )
    log.info("maintenance finished", extra=asdict(report))
    return report


def main() -> None:
    from shared.db import get_engine, init_db
    from shared.logging_setup import configure_logging

    configure_logging()
    engine = get_engine()
    init_db(engine, retries=10)
    report = run_checks(PgHsdsStore(engine), actor="cli")
    print(json.dumps(asdict(report), indent=2))


if __name__ == "__main__":
    main()
