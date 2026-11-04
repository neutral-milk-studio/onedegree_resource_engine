"""In-memory API over the fixtures, and the source of contracts/api/openapi.yaml.

It lets the review tool and other consumers build against the real contract before
the data core exists. State resets when the process restarts.
"""

from __future__ import annotations

import copy
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from contracts.findings import Category, ReviewState
from contracts.review import (
    CheckResult,
    Clash,
    ClashResolution,
    FieldChange,
    FindingDecision,
    FindingDismissal,
    ListResponse,
    RecordDetail,
    RecordPatch,
    RecordSummary,
    ReviewFinding,
    StoredRecord,
)
from contracts.store import IngestCounts, Page
from shared.hsds_validation import EXTENSION_KEY, HSDS_VERSION, find_issues

FIXTURES = Path(__file__).parent / "fixtures"
HIDDEN_STATUSES = {"inactive", "defunct"}
MOCK_REVIEWER = "mock-reviewer"
ISSUE_RESPONSE = {422: {"description": "The change fails HSDS validation. Nothing was saved."}}
CONFLICT_RESPONSE = {409: {"description": "The record changed since it was read."}}


def _reviewer(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(HTTPBearer())],
) -> str:
    """Accept any bearer token and act as one fixed reviewer."""
    return MOCK_REVIEWER


Reviewer = Annotated[str, Depends(_reviewer)]


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _find_object(value: Any, object_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("id") == object_id:
            return value
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = _find_object(child, object_id)
        if found is not None:
            return found
    return None


def _apply_change(service: dict[str, Any], change: FieldChange, actor: str) -> dict[str, Any]:
    """Return a copy of the service with the change and its history entry applied."""
    updated = copy.deepcopy(service)
    target = _find_object(updated, str(change.ref.entity_id))
    if target is None:
        raise HTTPException(404, f"Object {change.ref.entity_id} is not in this record")

    field = change.ref.field
    previous = copy.deepcopy(target.get(field))
    if change.ref.op == "set":
        if change.value is None:
            target.pop(field, None)
        else:
            target[field] = change.value
    elif change.ref.op == "add":
        item = dict(change.value)
        item.setdefault("id", str(uuid4()))
        target.setdefault(field, []).append(item)
    else:
        target[field] = [item for item in target.get(field, []) if item.get("id") != change.value]

    updated.setdefault("metadata", []).append(
        {
            "id": str(uuid4()),
            "resource_id": str(change.ref.entity_id),
            "resource_type": change.ref.entity_type,
            "last_action_date": datetime.now(UTC).isoformat(),
            "last_action_type": "update",
            "field_name": field,
            "previous_value": json.dumps(previous),
            "replacement_value": json.dumps(target.get(field)),
            "updated_by": actor,
        }
    )
    return updated


def create_app() -> FastAPI:
    app = FastAPI(title="The Resource Engine API contract", version=HSDS_VERSION)
    services = {
        record["id"]: StoredRecord(service=record, version=1)
        for record in (
            _load("coastside_hope.ndp.hsds.json"),
            _load("coastside_hope.odrs.hsds.json"),
        )
    }
    findings = {item["id"]: ReviewFinding.model_validate(item) for item in _load("findings.json")}
    clashes = {item["id"]: Clash.model_validate(item) for item in _load("clashes.json")}

    def get_stored(service_id: UUID) -> StoredRecord:
        stored = services.get(str(service_id))
        if stored is None:
            raise HTTPException(404, "Record not found")
        return stored

    def save(
        service_id: UUID, changes: list[FieldChange], expected_version: int, actor: str
    ) -> StoredRecord:
        stored = get_stored(service_id)
        if stored.version != expected_version:
            raise HTTPException(409, "The record changed since it was read. Reload and try again.")
        updated = stored.service
        for change in changes:
            updated = _apply_change(updated, change, actor)
        issues = find_issues(updated, "service")
        if issues:
            detail = [{"path": issue.path, "message": issue.message} for issue in issues]
            raise HTTPException(422, detail)
        result = StoredRecord(service=updated, version=stored.version + 1)
        services[str(service_id)] = result
        return result

    def summary(stored: StoredRecord) -> RecordSummary:
        record = stored.service
        return RecordSummary(
            id=record["id"],
            name=record["name"],
            organization_name=record.get("organization", {}).get("name"),
            source=record[EXTENSION_KEY]["source"]["system"],
            status=record["status"],
            version=stored.version,
            open_findings=sum(
                item.review_state is ReviewState.PENDING and str(item.service_id) == record["id"]
                for item in findings.values()
            ),
            last_check=_last_check(record["id"]),
        )

    def _last_check(service_id: str) -> CheckResult | None:
        matches = [item for item in findings.values() if str(item.service_id) == service_id]
        if not matches:
            return None
        return CheckResult(
            service_id=service_id,
            category=matches[0].category,
            checked_at=matches[0].created_at,
            finding_ids=[item.id for item in matches],
        )

    @app.get("/", tags=["hsds"])
    def api_info(_: Reviewer) -> dict[str, str]:
        return {
            "version": HSDS_VERSION,
            "profile": "https://docs.openreferral.org/en/latest/hsds/",
            "openapi_url": "/openapi.json",
            "description": "Mock of the Resource Engine API",
        }

    @app.get("/services", tags=["hsds"])
    def list_services(
        _: Reviewer,
        page: Annotated[int, Query(ge=1)] = 1,
        per_page: Annotated[int, Query(ge=1, le=200)] = 50,
        organization_id: UUID | None = None,
    ) -> Page:
        matches = [
            stored.service
            for stored in services.values()
            if stored.service["status"] not in HIDDEN_STATUSES
            and (
                organization_id is None
                or stored.service.get("organization_id") == str(organization_id)
            )
        ]
        start = (page - 1) * per_page
        contents = matches[start : start + per_page]
        total_pages = math.ceil(len(matches) / per_page)
        return Page(
            total_items=len(matches),
            total_pages=total_pages,
            page_number=page,
            size=len(contents),
            first_page=page == 1,
            last_page=page >= total_pages,
            empty=not contents,
            contents=contents,
        )

    @app.get("/services/{service_id}", tags=["hsds"])
    def get_service(service_id: UUID, _: Reviewer) -> dict[str, Any]:
        return get_stored(service_id).service

    @app.get("/review/records", tags=["review"])
    def list_records(
        _: Reviewer,
        category: Category | None = None,
        source: str | None = None,
        status: str | None = None,
    ) -> ListResponse[RecordSummary]:
        rows = [summary(stored) for stored in services.values()]
        matches = [
            row
            for row in rows
            if (source is None or row.source == source)
            and (status is None or row.status == status)
            and (
                category is None
                or any(
                    item.category is category and item.service_id == row.id
                    for item in findings.values()
                )
            )
        ]
        return ListResponse(total_items=len(matches), contents=matches)

    @app.get("/review/records/{record_id}", tags=["review"])
    def get_record_detail(record_id: UUID, _: Reviewer) -> RecordDetail:
        stored = get_stored(record_id)
        return RecordDetail(
            record=stored.service,
            version=stored.version,
            findings=[item for item in findings.values() if item.service_id == record_id],
            clashes=[item for item in clashes.values() if item.service_id == record_id],
            last_check=_last_check(str(record_id)),
        )

    @app.patch(
        "/review/records/{record_id}",
        tags=["review"],
        responses={**ISSUE_RESPONSE, **CONFLICT_RESPONSE},
    )
    def patch_record(record_id: UUID, patch: RecordPatch, actor: Reviewer) -> StoredRecord:
        return save(record_id, patch.changes, patch.expected_version, actor)

    @app.get("/review/findings", tags=["review"])
    def list_findings(
        _: Reviewer,
        category: Category | None = None,
        review_state: ReviewState | None = None,
    ) -> ListResponse[ReviewFinding]:
        matches = [
            item
            for item in findings.values()
            if (category is None or item.category is category)
            and (review_state is None or item.review_state is review_state)
        ]
        return ListResponse(total_items=len(matches), contents=matches)

    @app.post(
        "/review/findings/{finding_id}/apply",
        tags=["review"],
        responses={**ISSUE_RESPONSE, **CONFLICT_RESPONSE},
    )
    def apply_finding(
        finding_id: UUID, decision: FindingDecision, actor: Reviewer
    ) -> ReviewFinding:
        finding = findings.get(str(finding_id))
        if finding is None:
            raise HTTPException(404, "Finding not found")
        edited = decision.edited_value is not None
        value = decision.edited_value if edited else finding.proposed_value
        if value is None:
            raise HTTPException(422, "This finding has no proposed value, so send edited_value")
        change = FieldChange(ref=finding.ref, value=value)
        save(finding.service_id, [change], decision.expected_version, actor)
        finding.review_state = ReviewState.EDITED if edited else ReviewState.APPLIED
        return finding

    @app.post("/review/findings/{finding_id}/dismiss", tags=["review"])
    def dismiss_finding(
        finding_id: UUID, _dismissal: FindingDismissal, _: Reviewer
    ) -> ReviewFinding:
        finding = findings.get(str(finding_id))
        if finding is None:
            raise HTTPException(404, "Finding not found")
        finding.review_state = ReviewState.DISMISSED
        return finding

    @app.get("/review/clashes", tags=["review"])
    def list_clashes(_: Reviewer, status: str | None = None) -> ListResponse[Clash]:
        matches = [item for item in clashes.values() if status is None or item.status == status]
        return ListResponse(total_items=len(matches), contents=matches)

    @app.post(
        "/review/clashes/{clash_id}/resolve",
        tags=["review"],
        responses={**ISSUE_RESPONSE, **CONFLICT_RESPONSE},
    )
    def resolve_clash(clash_id: UUID, resolution: ClashResolution, actor: Reviewer) -> Clash:
        clash = clashes.get(str(clash_id))
        if clash is None:
            raise HTTPException(404, "Clash not found")
        value = {
            "source": clash.source_value,
            "current": clash.current_value,
            "custom": resolution.custom_value,
        }[resolution.choice]
        change = FieldChange(ref=clash.ref, value=value)
        save(clash.service_id, [change], resolution.expected_version, actor)
        clash.status = "resolved"
        return clash

    @app.get("/ingest/reports", tags=["review"])
    def ingest_reports(_: Reviewer) -> dict[str, IngestCounts]:
        return {
            "ndp": IngestCounts(received=1, stored=1, quarantined=0),
            "odrs": IngestCounts(received=1, stored=1, quarantined=0),
        }

    return app


app = create_app()
