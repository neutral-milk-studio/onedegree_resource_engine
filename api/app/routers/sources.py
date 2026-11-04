"""Add a partner: upload a spreadsheet, review the suggested mapping, confirm and import."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.app.auth import require_token
from api.app.deps import get_store
from api.app.models import IngestRunOut, QuarantineOut
from api.app.store_pg import PgHsdsStore
from ingest.adapters.registry import source_names
from ingest.adapters.spreadsheet import TARGETS
from ingest.partners import PartnerError, confirm, preview
from ingest.pipeline.run import run_ingest
from shared.models import SourceMapping

router = APIRouter(prefix="/sources", tags=["Add a partner"])
SAMPLE = Path(__file__).resolve().parents[3] / "data/samples/spreadsheet/county_food_pantries.csv"


class ColumnOut(BaseModel):
    column: str
    target: str
    confidence: float
    reason: str
    samples: list[str]


class PreviewOut(BaseModel):
    upload_id: str
    filename: str
    rows: int
    model_id: str
    note: str | None
    cost_usd: float
    columns: list[ColumnOut]
    targets: dict[str, str]


class ConfirmBody(BaseModel):
    upload_id: str
    partner_name: str = Field(min_length=1, max_length=120)
    mapping: dict[str, str]
    default_state: str = "CA"


class ConfirmOut(BaseModel):
    source: str
    run: IngestRunOut
    quarantine: list[QuarantineOut]


class SourceOut(BaseModel):
    name: str
    kind: str  # configured | partner
    partner_name: str | None = None
    confirmed_at: datetime | None = None


def _error(exc: PartnerError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@router.get("", response_model=list[SourceOut])
def list_sources(store: Annotated[PgHsdsStore, Depends(get_store)]) -> list[SourceOut]:
    out = [SourceOut(name=name, kind="configured") for name in source_names()]
    with store.session() as session:
        for row in session.scalars(select(SourceMapping).order_by(SourceMapping.partner_name)):
            out.append(SourceOut(name=row.source, kind="partner", partner_name=row.partner_name,
                                 confirmed_at=row.confirmed_at))  # fmt: skip
    return out


@router.get("/sample", response_class=PlainTextResponse)
def sample_file() -> PlainTextResponse:
    """The fictional messy county food pantry spreadsheet used in the demo."""
    return PlainTextResponse(SAMPLE.read_text(encoding="utf-8"), media_type="text/csv")


@router.post("/preview", response_model=PreviewOut)
async def preview_upload(
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
    file: Annotated[UploadFile, File(description="A CSV file")],
):
    """Upload a spreadsheet (CSV); get a suggested column-to-HSDS mapping with confidence."""
    content = await file.read()
    try:
        upload_id, rows, suggestion = preview(store, file.filename or "upload.csv", content, user)
    except PartnerError as exc:
        return _error(exc)
    return PreviewOut(
        upload_id=upload_id,
        filename=file.filename or "upload.csv",
        rows=rows,
        model_id=suggestion.model_id,
        note=suggestion.note,
        cost_usd=suggestion.cost_usd,
        columns=[ColumnOut(**c.__dict__) for c in suggestion.columns],
        targets=TARGETS,
    )


@router.post("/confirm", response_model=ConfirmOut)
def confirm_mapping(
    body: ConfirmBody,
    store: Annotated[PgHsdsStore, Depends(get_store)],
    user: Annotated[str, Depends(require_token)],
):
    """Save the mapping and import the rows now: raw saved, validated, stored or quarantined."""
    try:
        key, adapter = confirm(
            store, body.upload_id, body.partner_name, body.mapping, user, body.default_state
        )
    except PartnerError as exc:
        return _error(exc)
    report = run_ingest(adapter, store, triggered_by=user)
    return ConfirmOut(
        source=key,
        run=IngestRunOut.model_validate(store.get_run(report.run_id)),
        quarantine=[QuarantineOut.model_validate(q) for q in store.quarantined(report.run_id)],
    )
