"""Run AI maintenance checks on demand and read recent checks."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from api.app.auth import require_token
from api.app.deps import get_store
from api.app.store_pg import PgHsdsStore
from maintenance.worker.run_check import run_checks
from shared.models import Check

router = APIRouter(prefix="/maintenance", tags=["AI maintenance"])


class RunBody(BaseModel):
    service_ids: list[str] | None = None


class RunReport(BaseModel):
    checked: int
    by_category: dict[str, int]
    findings_created: int
    findings_refreshed: int
    findings_rejected: int
    model_calls: int
    cached: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    model_id: str
    elapsed_ms: int
    not_found: list[str]


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    service_id: str
    url: str | None
    checked_at: datetime
    category: str
    http_status: int | None
    error: str | None
    model_id: str | None
    cost_usd: float
    cached: bool
    findings_created: int


@router.post("/run", response_model=RunReport)
def run(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
    body: RunBody | None = None,
) -> RunReport:
    """Check records against their websites now (default: every record with a sample page)."""
    report = run_checks(store, service_ids=(body.service_ids if body else None), actor=user)
    return RunReport(**asdict(report))


@router.get("/checks", response_model=list[CheckOut])
def checks(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    service_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[CheckOut]:
    query = select(Check).order_by(Check.checked_at.desc()).limit(limit)
    if service_id:
        query = query.where(Check.service_id == service_id)
    with store.session() as session:
        return [CheckOut.model_validate(c) for c in session.scalars(query)]
