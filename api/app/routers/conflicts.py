"""Cross-source conflicts: list, detect, and resolve (choose which value wins)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from api.app.auth import require_token
from api.app.deps import get_store
from api.app.store_pg import PgHsdsStore
from ingest.pipeline.conflicts import ConflictError, detect_conflicts, resolve_conflict
from maintenance.comparator.compare import FIELD_LABELS
from shared.models import Conflict, Service

router = APIRouter(prefix="/conflicts", tags=["Conflicts"])
FIELD_LABELS = {**FIELD_LABELS, "email": "Email"}


class ServiceRef(BaseModel):
    id: str
    name: str


class Side(BaseModel):
    source: str
    value: Any | None
    values: list[str]
    organization_id: str
    services: list[ServiceRef]


class ConflictOut(BaseModel):
    id: str
    organization_name: str
    field: str
    field_label: str
    status: str
    a: Side
    b: Side
    detected_at: datetime
    resolution: str | None = None
    resolved_value: Any | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None


class ResolveBody(BaseModel):
    choice: Literal["a", "b", "custom"]
    custom_value: str | None = None


class Resolved(BaseModel):
    conflict: ConflictOut
    services_updated: int


class DetectOut(BaseModel):
    pairs: int
    opened: int
    refreshed: int
    closed: int


def _view(session, row: Conflict) -> ConflictOut:
    def side(source, value, values, org_id):
        services = session.scalars(
            select(Service).where(Service.organization_id == org_id).order_by(Service.name)
        ).all()
        return Side(
            source=source, value=value, values=values or [], organization_id=org_id,
            services=[ServiceRef(id=s.id, name=s.name) for s in services],
        )  # fmt: skip

    return ConflictOut(
        id=row.id,
        organization_name=row.organization_name,
        field=row.field,
        field_label=FIELD_LABELS.get(row.field, row.field),
        status=row.status,
        a=side(row.a_source, row.a_value, row.a_values, row.a_organization_id),
        b=side(row.b_source, row.b_value, row.b_values, row.b_organization_id),
        detected_at=row.detected_at,
        resolution=row.resolution,
        resolved_value=row.resolved_value,
        resolved_by=row.resolved_by,
        resolved_at=row.resolved_at,
    )


@router.get("", response_model=list[ConflictOut])
def list_conflicts(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    status: Literal["open", "resolved", "all"] = "open",
) -> list[ConflictOut]:
    query = select(Conflict).order_by(Conflict.organization_name, Conflict.field)
    if status != "all":
        query = query.where(Conflict.status == status)
    with store.session() as session:
        return [_view(session, row) for row in session.scalars(query)]


@router.post("/detect", response_model=DetectOut)
def detect(store: Annotated[PgHsdsStore, Depends(get_store)]) -> DetectOut:
    return DetectOut(**detect_conflicts(store).__dict__)


@router.post("/{conflict_id}/resolve", response_model=Resolved)
def resolve(
    conflict_id: str,
    body: ResolveBody,
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
):
    """Choose which value wins. It is applied to every service of the losing source(s)."""
    try:
        row, updated = resolve_conflict(store, conflict_id, body.choice, user, body.custom_value)
    except ConflictError as exc:
        detail: Any = exc.problems if exc.status == 422 and exc.problems else exc.message
        return JSONResponse({"detail": detail, "message": exc.message}, status_code=exc.status)
    with store.session() as session:
        return Resolved(
            conflict=_view(session, session.get(Conflict, row.id)), services_updated=updated
        )
