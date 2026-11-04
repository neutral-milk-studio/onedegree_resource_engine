"""Internal review API (contracts/api/openapi.yaml paths): findings list, apply, dismiss.

Responses use the committed ``contracts.review.ReviewFinding`` shape, extended with the
display fields the review queue needs (service name, harm rank, trust badge, version).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from api.app.auth import require_token
from api.app.deps import get_store
from api.app.routers.metrics import AuditOut
from api.app.store_pg import PgHsdsStore
from contracts.findings import Category, ReviewState
from contracts.review import (
    FieldRef,
    FindingDecision,
    FindingDismissal,
    ReviewEvidence,
    ReviewFinding,
)
from maintenance.comparator.compare import FIELD_LABELS
from maintenance.priority import harm_label
from maintenance.review import ReviewError, apply_finding, dismiss_finding, trust_by_service
from shared.models import Finding, Service

router = APIRouter(prefix="/review", tags=["Review"])


class TrustOut(BaseModel):
    score: int | None
    level: str
    badge: str
    checked_at: datetime | None


class QueueFinding(ReviewFinding):
    service_name: str
    organization_name: str | None
    source: str
    field: str | None
    field_label: str
    harm_rank: int
    harm_label: str
    record_version: int
    trust: TrustOut
    decided_by: str | None = None
    decided_at: datetime | None = None
    seconds_to_fix: float | None = None


class QueueList(BaseModel):
    total_items: int
    counts_by_category: dict[str, int]
    contents: list[QueueFinding]


class Decided(BaseModel):
    finding: QueueFinding
    record_version: int
    audit_entry: AuditOut


class ApplyBody(FindingDecision):
    seconds_to_fix: float | None = None


class DismissBody(FindingDismissal):
    seconds_to_fix: float | None = None


def to_view(row: Finding, service: Service, trust) -> QueueFinding:
    signal = row.category in {"actively_bad", "check_failed"} and (row.error or row.http_status)
    evidence = ReviewEvidence(
        kind="http_signal" if signal else "quote",
        source_type="website",
        url=row.evidence_url,
        fetched_at=row.last_seen_at,
        snippet=row.evidence_quote,
        http_status=row.http_status,
        error=row.error,
    )
    return QueueFinding(
        id=row.id,
        service_id=row.service_id,
        ref=FieldRef(entity_type=row.entity_type, entity_id=row.entity_id, field=row.entity_field),
        category=Category(row.category),
        current_value=row.current_value,
        proposed_value=row.proposed_value,
        evidence=[evidence],
        confidence=row.confidence,
        model_id=row.model_id or None,
        created_at=row.created_at,
        review_state=ReviewState(row.review_state),
        service_name=service.name,
        organization_name=service.organization_name,
        source=service.source,
        field=row.field,
        field_label=FIELD_LABELS.get(row.field or "", "Record"),
        harm_rank=row.harm_rank,
        harm_label=harm_label(row.harm_rank),
        record_version=service.version,
        trust=TrustOut(**trust.__dict__),
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        seconds_to_fix=row.seconds_to_fix,
    )


@router.get("/findings", response_model=QueueList)
def list_findings(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    category: Category | None = None,
    state: Literal["pending", "decided", "all"] = "pending",
    sort: Literal["harm", "newest"] = "harm",
    service_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> QueueList:
    """Findings, most harmful first by default. ``state=pending`` is the review queue."""
    query = select(Finding, Service).join(Service, Service.id == Finding.service_id)
    if state == "pending":
        query = query.where(Finding.review_state == "pending")
    elif state == "decided":
        query = query.where(Finding.review_state != "pending")
    if service_id:
        query = query.where(Finding.service_id == service_id)
    with store.session() as session:
        rows = session.execute(query).all()
        counts: dict[str, int] = {}
        for finding, _ in rows:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        if category:
            rows = [r for r in rows if r[0].category == category.value]
        if sort == "harm":
            rows.sort(key=lambda r: (r[0].harm_rank, -r[0].confidence, r[0].created_at))
        else:
            rows.sort(key=lambda r: r[0].created_at, reverse=True)
        trust = trust_by_service(session, list({f.service_id for f, _ in rows}))
        contents = [to_view(f, s, trust[f.service_id]) for f, s in rows[:limit]]
    return QueueList(total_items=len(rows), counts_by_category=counts, contents=contents)


def _decided(store: PgHsdsStore, decision) -> Decided:
    with store.session() as session:
        finding = session.get(Finding, decision.finding.id)
        service = session.get(Service, finding.service_id)
        trust = trust_by_service(session, [service.id])[service.id]
        return Decided(
            finding=to_view(finding, service, trust),
            record_version=service.version,
            audit_entry=AuditOut.model_validate(decision.audit),
        )


def _error(exc: ReviewError) -> JSONResponse:
    detail: Any = exc.problems if exc.status == 422 and exc.problems else exc.message
    return JSONResponse({"detail": detail, "message": exc.message}, status_code=exc.status)


@router.post("/findings/{finding_id}/apply", response_model=Decided)
def apply(
    finding_id: str,
    body: ApplyBody,
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
):
    """Apply the proposed value (or ``edited_value``). Re-validates HSDS before saving."""
    try:
        decision = apply_finding(
            store, finding_id, body.expected_version, user, body.edited_value, body.seconds_to_fix
        )
    except ReviewError as exc:
        return _error(exc)
    return _decided(store, decision)


@router.post("/findings/{finding_id}/dismiss", response_model=Decided)
def dismiss(
    finding_id: str,
    body: DismissBody,
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
):
    """Keep the current value. The record is unchanged; the decision is audited."""
    try:
        decision = dismiss_finding(store, finding_id, user, body.reason, body.seconds_to_fix)
    except ReviewError as exc:
        return _error(exc)
    return _decided(store, decision)


@router.get("/records/{service_id}/history", response_model=list[AuditOut])
def record_history(service_id: str, store: Annotated[PgHsdsStore, Depends(get_store)]):
    from shared.models import AuditEntry

    with store.session() as session:
        if session.get(Service, service_id) is None:
            raise HTTPException(404, f"No service with id {service_id}")
        rows = session.scalars(
            select(AuditEntry)
            .where(AuditEntry.entity_type == "service", AuditEntry.entity_id == service_id)
            .order_by(AuditEntry.at.desc())
        ).all()
        return [AuditOut.model_validate(r) for r in rows]
