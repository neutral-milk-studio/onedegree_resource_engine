"""Read-only HSDS endpoints: ``GET /services`` and ``GET /services/{id}``."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from api.app.deps import get_store
from api.app.models import ServicePage
from api.app.store_pg import PgHsdsStore
from contracts.store import ServiceFilter

router = APIRouter(tags=["HSDS"])


@router.get("/services", response_model=ServicePage)
def list_services(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1,
    per_page: Annotated[int, Query(ge=1, le=200)] = 50,
    organization_id: UUID | None = None,
    modified_after: datetime | None = None,
    source: Annotated[str | None, Query(max_length=64)] = None,
) -> ServicePage:
    """Paginated HSDS services. Supplied filters combine with AND."""
    filt = ServiceFilter(
        organization_id=str(organization_id) if organization_id else None,
        modified_after=modified_after.isoformat() if modified_after else None,
        source=source,
    )
    result = store.list_services(filt, page, per_page)
    return ServicePage(**result.__dict__)


@router.get("/services/{service_id}")
def get_service(
    service_id: UUID, store: Annotated[PgHsdsStore, Depends(get_store)]
) -> dict[str, Any]:
    """One fully nested HSDS service."""
    record = store.get_service(str(service_id))
    if record is None:
        raise HTTPException(404, f"No service with id {service_id}")
    return record
