"""GET /search: plain-language search over active, trusted listings."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.app.deps import get_store
from api.app.routers.review import TrustOut
from api.app.store_pg import PgHsdsStore
from maintenance.review import trust_by_service
from search.query.intent import plan
from search.query.search import get_backend

router = APIRouter(tags=["Search"])


class ResultOut(BaseModel):
    service_id: str
    name: str
    organization_name: str | None
    snippet: str
    city: str | None
    phone: str | None
    website: str | None
    source: str
    score: float
    latitude: float | None
    longitude: float | None
    trust: TrustOut


class SearchOut(BaseModel):
    query: str
    needs: list[str]
    own_terms: list[str]
    added_terms: list[str]
    total: int
    hidden: int
    backend: str
    results: list[ResultOut]


@router.get("/search", response_model=SearchOut)
def search(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    q: Annotated[str, Query(min_length=2, max_length=300, description="Plain-language request")],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SearchOut:
    """Keyword search (semantic later). Never returns listings flagged suspicious or actively
    bad, or inactive ones; ``hidden`` says how many matching listings were left out."""
    query_plan = plan(q)
    results = get_backend(store).search(query_plan, limit, offset)
    with store.session() as session:
        trust = trust_by_service(session, [h.service_id for h in results.hits])
    return SearchOut(
        query=q,
        needs=query_plan.needs,
        own_terms=query_plan.own_terms,
        added_terms=query_plan.added_terms,
        total=results.total,
        hidden=results.hidden,
        backend=results.backend,
        results=[
            ResultOut(**h.__dict__, trust=TrustOut(**trust[h.service_id].__dict__))
            for h in results.hits
        ],  # fmt: skip
    )
