"""Pydantic response models for the HSDS Page wrapper and the ingest endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ServicePage(BaseModel):
    """HSDS Page wrapper. ``contents`` are HSDS v3.2.3 service records."""

    total_items: int
    total_pages: int
    page_number: int
    size: int
    first_page: bool
    last_page: bool
    empty: bool
    contents: list[dict[str, Any]]


class IngestRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source: str
    status: str
    triggered_by: str
    started_at: datetime
    finished_at: datetime | None
    elapsed_ms: int | None
    received: int
    stored: int
    quarantined: int
    services_stored: int
    objects_total: int
    objects_kept: int
    objects_quarantined: int
    error: str | None = None


class QuarantineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    native_id: str
    level: str
    kind: str
    object_id: str | None
    reasons: list[str]
    raw_ref: str | None


class SourceOut(BaseModel):
    name: str
    description: str | None = None
