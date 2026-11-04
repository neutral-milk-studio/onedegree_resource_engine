"""SQLAlchemy models for the HSDS store (``hsds`` schema) and operational data (``ops`` schema).

HSDS records and operational data never share a table (docs/agents-guardrails.md section 6).

- ``hsds.objects``: every HSDS object exactly as the mapper produced it (flat, linked by id).
  Shared organizations and locations are stored once.
- ``hsds.services``: one row per service with the fully nested HSDS service document plus a
  few key columns for filtering, search and the dashboard.
- ``hsds.id_map``: HSDS id -> source record.
- ``ops.*``: ingest runs, quarantine, audit log. Findings and conflicts arrive in later steps.

The same models run on PostgreSQL (docker compose) and SQLite (unit tests): JSON columns
become JSONB on Postgres, and the schema names are dropped on SQLite.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

HSDS_SCHEMA = os.environ.get("RE_HSDS_SCHEMA", "hsds")
OPS_SCHEMA = os.environ.get("RE_OPS_SCHEMA", "ops")

Json = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --- HSDS store ------------------------------------------------------------------------------


class HsdsObject(Timestamped, Base):
    """One HSDS object of any kind, keyed by its deterministic id."""

    __tablename__ = "objects"
    __table_args__ = {"schema": HSDS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    record_native_id: Mapped[str] = mapped_column(String(128), index=True)
    # Parent links copied out of the body so assembly can look children up by index.
    organization_id: Mapped[str | None] = mapped_column(String(36), index=True)
    service_id: Mapped[str | None] = mapped_column(String(36), index=True)
    location_id: Mapped[str | None] = mapped_column(String(36), index=True)
    service_at_location_id: Mapped[str | None] = mapped_column(String(36), index=True)
    link_id: Mapped[str | None] = mapped_column(String(36), index=True)
    body: Mapped[dict[str, Any]] = mapped_column(Json)
    content_hash: Mapped[str] = mapped_column(String(64))


class Service(Timestamped, Base):
    """A served HSDS service: the nested document plus key columns."""

    __tablename__ = "services"
    __table_args__ = {"schema": HSDS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    native_id: Mapped[str] = mapped_column(String(128))
    record_native_id: Mapped[str] = mapped_column(String(128))
    organization_id: Mapped[str | None] = mapped_column(String(36), index=True)
    organization_name: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    website: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(Text)
    postal_code: Mapped[str | None] = mapped_column(String(16))
    last_modified: Mapped[str | None] = mapped_column(String(40))
    assured_date: Mapped[str | None] = mapped_column(String(10))
    document: Mapped[dict[str, Any]] = mapped_column(Json)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)


class IdMap(Base):
    """HSDS id -> the source record it came from (docs: contracts/hsds/extensions.md)."""

    __tablename__ = "id_map"
    __table_args__ = {"schema": HSDS_SCHEMA}

    hsds_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(64), index=True)
    native_id: Mapped[str] = mapped_column(String(128))
    record_native_id: Mapped[str] = mapped_column(String(128))


# --- Operational data -----------------------------------------------------------------------


class IngestRun(Base):
    """Counts for one ingest run. Invariant: received == stored + quarantined."""

    __tablename__ = "ingest_runs"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|succeeded|failed
    triggered_by: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    received: Mapped[int] = mapped_column(Integer, default=0)
    stored: Mapped[int] = mapped_column(Integer, default=0)
    quarantined: Mapped[int] = mapped_column(Integer, default=0)
    services_stored: Mapped[int] = mapped_column(Integer, default=0)
    objects_total: Mapped[int] = mapped_column(Integer, default=0)
    objects_kept: Mapped[int] = mapped_column(Integer, default=0)
    objects_quarantined: Mapped[int] = mapped_column(Integer, default=0)
    raw_refs: Mapped[list[str]] = mapped_column(Json, default=list)
    error: Mapped[str | None] = mapped_column(Text)


class QuarantineEntry(Base):
    """A record or object that could not be stored, with every reason.

    ``level='record'``: the whole source record was held back (mapping failed, or its
    organization/service was invalid). ``level='object'``: one child object (e.g. an address
    with no city) was held back while the rest of the record was stored.
    """

    __tablename__ = "quarantine"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    native_id: Mapped[str] = mapped_column(String(128))
    level: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str | None] = mapped_column(String(36))
    reasons: Mapped[list[str]] = mapped_column(Json)
    payload: Mapped[dict[str, Any]] = mapped_column(Json)
    raw_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEntry(Base):
    """Who changed what, from what, to what, when, and where the change came from."""

    __tablename__ = "audit_log"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(40))  # ingest_run | apply_finding | ...
    change_source: Mapped[str] = mapped_column(String(32))  # ingest | ai | specialist | conflict
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(128))
    field: Mapped[str | None] = mapped_column(String(128))
    old_value: Mapped[Any | None] = mapped_column(Json)
    new_value: Mapped[Any | None] = mapped_column(Json)
    summary: Mapped[str | None] = mapped_column(Text)
