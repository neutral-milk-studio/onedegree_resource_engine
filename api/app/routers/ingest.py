"""Ingest endpoints: run a configured source on demand and read run reports."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.app.auth import require_token
from api.app.deps import get_store
from api.app.models import IngestRunOut, QuarantineOut, SourceOut
from api.app.store_pg import PgHsdsStore
from ingest.adapters.registry import UnknownSource, get_adapter, source_names
from ingest.pipeline.run import run_ingest

router = APIRouter(prefix="/ingest", tags=["Ingest"])
log = logging.getLogger(__name__)


@router.get("/sources", response_model=list[SourceOut])
def list_sources() -> list[SourceOut]:
    return [SourceOut(name=name) for name in source_names()]


@router.post("/{source}", response_model=IngestRunOut)
def ingest_source(
    source: str,
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
) -> IngestRunOut:
    """Run one ingest now. Re-running updates records in place (deterministic ids)."""
    try:
        adapter = get_adapter(source)
    except UnknownSource as exc:
        from ingest.partners import partner_adapter

        adapter = partner_adapter(store, source)  # a partner added through the dashboard
        if adapter is None:
            raise HTTPException(404, str(exc.args[0])) from exc
    try:
        report = run_ingest(adapter, store, triggered_by=user)
    except FileNotFoundError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"ingest of {source!r} failed: {exc}") from exc
    return IngestRunOut.model_validate(store.get_run(report.run_id))


@router.get("/runs", response_model=list[IngestRunOut])
def list_runs(store: Annotated[PgHsdsStore, Depends(get_store)]) -> list[IngestRunOut]:
    return [IngestRunOut.model_validate(r) for r in store.list_runs()]


@router.get("/runs/{run_id}/quarantine", response_model=list[QuarantineOut])
def run_quarantine(
    run_id: str, store: Annotated[PgHsdsStore, Depends(get_store)]
) -> list[QuarantineOut]:
    if store.get_run(run_id) is None:
        raise HTTPException(404, f"No ingest run with id {run_id}")
    return [QuarantineOut.model_validate(q) for q in store.quarantined(run_id)]
