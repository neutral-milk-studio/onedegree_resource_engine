"""GET /coverage: which common needs have no visible, active service in each city.

Turns data already ingested into a staff-facing gap report -- no new taxonomy, no new
ingest, just a different read of what's there (search/query/coverage.py).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.app.deps import get_store
from api.app.store_pg import PgHsdsStore
from search.query.coverage import coverage_by_city
from search.query.intent import INTENTS

router = APIRouter(tags=["Coverage"])


class CityCoverageOut(BaseModel):
    city: str
    service_count: int
    covered: list[str]
    missing: list[str]


class CoverageOut(BaseModel):
    needs: list[str]
    cities: list[CityCoverageOut]


@router.get("/coverage", response_model=CoverageOut)
def coverage(store: Annotated[PgHsdsStore, Depends(get_store)]) -> CoverageOut:
    rows = coverage_by_city(store)
    return CoverageOut(
        needs=list(INTENTS),
        cities=[CityCoverageOut(**r.__dict__) for r in rows],
    )
