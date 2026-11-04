"""Dashboard numbers (``GET /metrics``) and recent activity (``GET /audit``).

Every number comes from the database. Values that depend on later features (AI checks,
conflicts) are ``null`` until those features have produced data, so the dashboard can say
"not run yet" instead of showing a misleading zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from api.app.deps import get_store
from api.app.store_pg import PgHsdsStore
from shared.models import AuditEntry, IngestRun, Service

router = APIRouter(tags=["Dashboard"])

# Harm order, most harmful first (README "Harm-based priority").
FINDING_CATEGORIES = [
    "actively_bad",
    "suspicious",
    "discrepancy",
    "missing_information",
    "check_failed",
    "verified",
]


class SourceCount(BaseModel):
    source: str
    records: int


class LastIngest(BaseModel):
    source: str
    finished_at: datetime | None
    received: int
    stored: int
    quarantined: int
    objects_quarantined: int


class FindingsMetrics(BaseModel):
    checks_run: int
    open_total: int
    open_by_category: dict[str, int]


class Metrics(BaseModel):
    generated_at: datetime
    total_records: int
    records_by_source: list[SourceCount]
    records_by_status: dict[str, int]
    quarantined_records: int
    records_missing_information: int
    quarantined_objects: int
    last_ingest: list[LastIngest]
    findings: FindingsMetrics
    conflicts_open: int | None
    avg_trust_score: float | None
    cost_per_check_usd: float | None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    at: datetime
    actor: str
    action: str
    change_source: str
    entity_type: str
    entity_id: str
    field: str | None
    old_value: Any | None
    new_value: Any | None
    summary: str | None


def compute_metrics(store: PgHsdsStore) -> Metrics:
    with store.session() as s:
        by_source = s.execute(
            select(Service.source, func.count()).group_by(Service.source).order_by(Service.source)
        ).all()
        by_status = {
            status: n
            for status, n in s.execute(
                select(Service.status, func.count()).group_by(Service.status)
            )
        }

        # Records whose organization was stored with a placeholder (e.g. ODRS has no
        # organization description): flagged at ingest, fixed through review.
        missing_info = sum(
            1
            for (doc,) in s.execute(select(Service.document))
            if any(
                str(flag).startswith("missing_information")
                for flag in (
                    (doc.get("organization") or {}).get("x_resource_engine", {}).get("flags", [])
                )
            )
        )

        # The latest successful run per source describes the current state of that source.
        latest = (
            select(IngestRun.source, func.max(IngestRun.started_at).label("started_at"))
            .where(IngestRun.status == "succeeded")
            .group_by(IngestRun.source)
            .subquery()
        )
        runs = s.scalars(
            select(IngestRun)
            .join(
                latest,
                (IngestRun.source == latest.c.source)
                & (IngestRun.started_at == latest.c.started_at),
            )
            .order_by(IngestRun.source)
        ).all()

    return Metrics(
        generated_at=datetime.now(UTC),
        total_records=sum(count for _, count in by_source),
        records_by_source=[SourceCount(source=src, records=n) for src, n in by_source],
        records_by_status=by_status,
        quarantined_records=sum(r.quarantined for r in runs),
        records_missing_information=missing_info,
        quarantined_objects=sum(r.objects_quarantined for r in runs),
        last_ingest=[
            LastIngest(
                source=r.source,
                finished_at=r.finished_at,
                received=r.received,
                stored=r.stored,
                quarantined=r.quarantined,
                objects_quarantined=r.objects_quarantined,
            )
            for r in runs
        ],
        # AI maintenance arrives in step 3; conflicts in step 4.
        findings=FindingsMetrics(
            checks_run=0, open_total=0, open_by_category=dict.fromkeys(FINDING_CATEGORIES, 0)
        ),
        conflicts_open=None,
        avg_trust_score=None,
        cost_per_check_usd=None,
    )


@router.get("/metrics", response_model=Metrics)
def metrics(store: Annotated[PgHsdsStore, Depends(get_store)]) -> Metrics:
    return compute_metrics(store)


@router.get("/audit", response_model=list[AuditOut])
def audit(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> list[AuditOut]:
    """Most recent audit-log entries first."""
    with store.session() as s:
        rows = s.scalars(select(AuditEntry).order_by(AuditEntry.at.desc()).limit(limit)).all()
        return [AuditOut.model_validate(r) for r in rows]
