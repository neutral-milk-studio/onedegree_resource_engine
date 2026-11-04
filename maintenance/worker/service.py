"""The check loop: select records due -> fetch -> extract -> compare -> findings and check result.

Extraction and comparison are maintenance.extractor and maintenance.comparator. Nothing here
changes a record: findings wait in change requests for a person.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from api.app.hsds.nest import Views
from contracts.findings import Category
from maintenance.comparator.compare import check_page, failure, site_loaded
from maintenance.extractor.extract import ExtractionError, extract_facts
from maintenance.findings_store.change_requests import ChangeRequestSink
from shared.db import dumps, new_id, now_iso

CATEGORIES = [category.value for category in Category]
NO_SAMPLE = "No sample page exists for this website yet, so it wasn't checked."
NO_WEBSITE = "No website on record to check against."


class MaintenanceService:
    def __init__(self, settings, db, store, proposals, website, invoke=None):
        self.s, self.db, self.store, self.website, self.invoke = (
            settings,
            db,
            store,
            website,
            invoke,
        )
        self.sink = ChangeRequestSink(store, proposals)

    def due_services(self, limit: int | None = None) -> list[dict]:
        cutoff = datetime.now(UTC) - timedelta(days=self.s.maintenance_recheck_days)
        rows = self.db.q(
            "SELECT r.id FROM resource r LEFT JOIN check_result c ON c.resource_id=r.id "
            "WHERE r.type='service' AND r.deleted=0 AND (c.checked_at IS NULL OR c.checked_at < ?) "
            "ORDER BY COALESCE(c.checked_at,'') ASC, r.id",
            (cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z"),),
        )
        return [self.store.get(row["id"]) for row in rows][: limit or None]

    def run(
        self,
        scope: str = "all",
        service_id: str | None = None,
        trigger: str = "scheduled",
        initiated_by: str = "scheduler",
        limit: int | None = None,
        force: bool = False,
    ) -> dict:
        run_id = new_id()
        self.db.run(
            "INSERT INTO maintenance_run (id,scope,trigger,initiated_by,started_at,status) "
            "VALUES (?,?,?,?,?,'running')",
            (
                run_id,
                scope if scope == "all" else f"service:{service_id}",
                trigger,
                initiated_by,
                now_iso(),
            ),
        )
        if scope == "service":
            row = self.store.get(service_id)
            rows, force = ([row] if row and row["type"] == "service" else []), True
        elif force:
            rows = self.store.all_of_type("service")[: limit or None]
        else:
            rows = self.due_services(limit)
        counts = dict.fromkeys(CATEGORIES, 0) | {"checked": 0, "proposals": 0, "skipped": 0}
        pages: dict[str, tuple] = {}
        for row in rows:
            out = self.check_service(row, pages, run_id, force)
            counts["checked"] += 1
            if out.get("skipped"):
                counts["skipped"] += 1
            else:
                counts[out["category"]] += 1
                counts["proposals"] += out["proposals"]
        self.db.run(
            "UPDATE maintenance_run SET finished_at=?, status='completed', counts=? WHERE id=?",
            (now_iso(), dumps(counts), run_id),
        )
        return {"run_id": run_id, **counts}

    def _nested(self, row: dict) -> dict:
        views = Views(self.store)
        views.prefetch([row])
        return views.service(row, ext=True)

    def _url(self, row: dict) -> tuple[str | None, bool]:
        """The website to check, and whether the website source has content for it."""
        org = self.store.get(row["org_id"]) if row["org_id"] else None
        org_data = (org or {}).get("data", {})
        candidates = [org_data.get("website"), row["data"].get("url"), org_data.get("url")]
        candidates = [url for url in candidates if url]
        if not candidates:
            return None, False
        knows = getattr(self.website, "knows", None)
        if knows is None:
            return candidates[0], True
        known = next((url for url in candidates if knows(url)), None)
        return known or candidates[0], known is not None

    def check_service(self, row: dict, pages: dict, run_id: str, force: bool = False) -> dict:
        url, available = self._url(row)
        if not url or not available:
            summary = NO_WEBSITE if not url else NO_SAMPLE
            return self._finish(row, "check_failed", summary, url, run_id, "", None, None, 0)

        prior = self.db.one("SELECT * FROM check_result WHERE resource_id=?", (row["id"],))
        if url not in pages:
            site = self.website.get(url)
            pages[url] = (site, None, None)
        site, extraction, extract_error = pages[url]
        page_hash = None
        if site_loaded(site):
            page_hash = hashlib.sha256(site.content.encode()).hexdigest()[:16]
            unchanged = (
                prior
                and prior["page_hash"] == page_hash
                and prior["record_version"] == row["version"]
                and prior["category"] == Category.VERIFIED.value
            )
            if unchanged and not force:
                self.db.run(
                    "UPDATE check_result SET checked_at=?, run_id=? WHERE resource_id=?",
                    (now_iso(), run_id, row["id"]),
                )
                return {"skipped": True}
            if extraction is None and extract_error is None:
                try:
                    extraction = extract_facts(site.content, invoke=self.invoke)
                except ExtractionError as exc:  # a failed check is a finding, never a silent skip
                    extract_error = str(exc)
                pages[url] = (site, extraction, extract_error)

        record, checked_at = self._nested(row), now_iso()
        if extract_error:
            result = failure(
                record, Category.CHECK_FAILED, extract_error, url=url, checked_at=checked_at
            )
        else:
            failed_before = bool(
                prior
                and prior["category"] == Category.CHECK_FAILED.value
                and not prior["page_hash"]
            )
            result = check_page(
                record,
                site,
                extraction,
                url=url,
                checked_at=checked_at,
                unreachable_before=failed_before,
            )
        for finding in result.findings:
            self.sink.save(finding)
        proposals = sum(item.category is not Category.VERIFIED for item in result.findings)
        summary = "; ".join(
            dict.fromkeys(
                f"{item.field}: {item.category.value}" if item.field else item.evidence[0].snippet
                for item in result.findings
            )
        )
        model = extraction["model_id"] if extraction else ""
        return self._finish(
            row,
            result.category.value,
            summary[:500],
            url,
            run_id,
            model,
            page_hash,
            row["version"],
            proposals,
        )

    def _finish(
        self, row, category, summary, url, run_id, model, page_hash, version, proposals
    ) -> dict:
        self.db.run(
            "INSERT INTO check_result (resource_id,resource_type,org_id,category,summary,url,"
            "checked_at,run_id,model,verified_marker,page_hash,record_version) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(resource_id) DO UPDATE SET "
            "category=excluded.category, summary=excluded.summary, url=excluded.url, "
            "checked_at=excluded.checked_at, run_id=excluded.run_id, model=excluded.model, "
            "verified_marker=excluded.verified_marker, page_hash=excluded.page_hash, "
            "record_version=excluded.record_version",
            (
                row["id"],
                "service",
                row["org_id"],
                category,
                summary,
                url,
                now_iso(),
                run_id,
                model,
                int(category == Category.VERIFIED.value),
                page_hash,
                version,
            ),
        )
        return {"category": category, "proposals": proposals, "summary": summary}
