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
from maintenance.review import trust_by_service
from shared.models import AuditEntry, Check, Conflict, Finding, IngestRun, Service

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
    records_checked: int
    open_total: int
    # Open (pending) findings per category; "verified" = records whose latest check matched.
    open_by_category: dict[str, int]
    decided: int
    median_seconds_to_fix: float | None
    model_ids: list[str]


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

        open_counts = dict.fromkeys(FINDING_CATEGORIES, 0)
        for category, n in s.execute(
            select(Finding.category, func.count())
            .where(Finding.review_state == "pending")
            .group_by(Finding.category)
        ):
            open_counts[category] = n
        trust = trust_by_service(s)
        open_counts["verified"] = sum(1 for t in trust.values() if t.badge.startswith("Verified"))
        checks_run, total_cost = s.execute(
            select(func.count(Check.id), func.coalesce(func.sum(Check.cost_usd), 0.0))
        ).one()
        model_ids = sorted(
            m for (m,) in s.execute(select(Check.model_id).distinct()) if m is not None
        )
        fix_times = sorted(
            v
            for (v,) in s.execute(
                select(Finding.seconds_to_fix).where(Finding.seconds_to_fix.is_not(None))
            )
        )
        decided = s.scalar(
            select(func.count()).select_from(Finding).where(Finding.review_state != "pending")
        )
        scores = [t.score for t in trust.values() if t.score is not None]
        conflicts_open = s.scalar(
            select(func.count()).select_from(Conflict).where(Conflict.status == "open")
        )

    median_fix = None
    if fix_times:
        mid = len(fix_times) // 2
        median_fix = (
            fix_times[mid] if len(fix_times) % 2 else (fix_times[mid - 1] + fix_times[mid]) / 2
        )

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
        findings=FindingsMetrics(
            checks_run=checks_run,
            records_checked=len(trust),
            open_total=sum(n for c, n in open_counts.items() if c != "verified"),
            open_by_category=open_counts,
            decided=decided or 0,
            median_seconds_to_fix=median_fix,
            model_ids=model_ids,
        ),
        conflicts_open=conflicts_open or 0,
        avg_trust_score=round(sum(scores) / len(scores), 1) if scores else None,
        cost_per_check_usd=(total_cost / checks_run) if checks_run else None,
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
