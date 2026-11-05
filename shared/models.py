"""SQLAlchemy models for the HSDS store (``hsds`` schema) and operational data (``ops`` schema).

HSDS records and operational data never share a table (docs/agents-guardrails.md section 6).

- ``hsds.objects``: every HSDS object exactly as the mapper produced it (flat, linked by id).
  Shared organizations and locations are stored once.
- ``hsds.services``: one row per service with the fully nested HSDS service document plus a
  few key columns for filtering, search and the dashboard.
- ``hsds.id_map``: HSDS id -> source record.
- ``ops.*``: ingest runs, quarantine, audit log, website checks, findings, the model-response
  cache. Conflicts arrive in step 4.

The same models run on PostgreSQL (docker compose) and SQLite (unit tests): JSON columns
become JSONB on Postgres, and the schema names are dropped on SQLite.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, LargeBinary, String, Text, func
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
    # Changes a person applied (field -> {"value", "base", "at", "by"}). Re-applied after every
    # re-ingest so a source refresh never silently undoes a specialist's fix.
    overrides: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    # Derived from the document (search/indexer/build_index.py); rebuilt on every save.
    search_text: Mapped[str | None] = mapped_column(Text)
    # Semantic search vector (search/embeddings/embed.py), with the model that produced it,
    # so a model change (offline <-> Bedrock) re-embeds instead of comparing incompatible
    # vectors. Rebuilt on every save that changes the record.
    embedding: Mapped[list[float] | None] = mapped_column(Json)
    embedding_model_id: Mapped[str | None] = mapped_column(String(64))


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


class Check(Base):
    """One AI maintenance check of one service against its website."""

    __tablename__ = "checks"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_id: Mapped[str] = mapped_column(String(36), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    category: Mapped[str] = mapped_column(String(32))  # the one category for this check
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    page_hash: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(200))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    findings_created: Mapped[int] = mapped_column(Integer, default=0)
    risk_signals: Mapped[list[str]] = mapped_column(Json, default=list)


class Finding(Base):
    """An AI finding awaiting (or after) human review. Never written into the HSDS record."""

    __tablename__ = "findings"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    service_id: Mapped[str] = mapped_column(String(36), index=True)
    check_id: Mapped[str] = mapped_column(String(36), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    field: Mapped[str | None] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(36))
    entity_field: Mapped[str] = mapped_column(String(64))
    current_value: Mapped[Any | None] = mapped_column(Json)
    proposed_value: Mapped[Any | None] = mapped_column(Json)
    evidence_url: Mapped[str] = mapped_column(Text)
    evidence_quote: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float)
    model_id: Mapped[str | None] = mapped_column(String(200))
    harm_rank: Mapped[int] = mapped_column(Integer, index=True)
    risk_signals: Mapped[list[str]] = mapped_column(Json, default=list)
    # 0-100, suspicious findings only (maintenance/risk.py); null for every other category.
    risk_score: Mapped[int | None] = mapped_column(Integer)
    review_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(128))
    decision_value: Mapped[Any | None] = mapped_column(Json)
    dismiss_reason: Mapped[str | None] = mapped_column(Text)
    seconds_to_fix: Mapped[float | None] = mapped_column(Float)


class LlmCache(Base):
    """Model replies keyed by a hash of (prompt version, model, page text, record facts)."""

    __tablename__ = "llm_cache"
    __table_args__ = {"schema": OPS_SCHEMA}

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(200))
    value: Mapped[dict[str, Any]] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conflict(Base):
    """The same organization from two sources with different values for one field."""

    __tablename__ = "conflicts"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    match_key: Mapped[str] = mapped_column(String(200), index=True)
    organization_name: Mapped[str] = mapped_column(Text)
    field: Mapped[str] = mapped_column(String(40))
    a_source: Mapped[str] = mapped_column(String(64))
    a_organization_id: Mapped[str] = mapped_column(String(36))
    a_value: Mapped[Any | None] = mapped_column(Json)
    a_values: Mapped[list[str]] = mapped_column(Json, default=list)
    b_source: Mapped[str] = mapped_column(String(64))
    b_organization_id: Mapped[str] = mapped_column(String(36))
    b_value: Mapped[Any | None] = mapped_column(Json)
    b_values: Mapped[list[str]] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolution: Mapped[str | None] = mapped_column(String(16))  # a | b | custom | now_match
    resolved_value: Mapped[Any | None] = mapped_column(Json)
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceUpload(Base):
    """A partner spreadsheet exactly as uploaded (also written to the raw store on ingest)."""

    __tablename__ = "source_uploads"
    __table_args__ = {"schema": OPS_SCHEMA}

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(Text)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    rows: Mapped[int] = mapped_column(Integer)
    suggestion: Mapped[dict[str, Any]] = mapped_column(Json)
    uploaded_by: Mapped[str] = mapped_column(String(128))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceMapping(Base):
    """A confirmed partner source: its column mapping and the upload it was confirmed on."""

    __tablename__ = "source_mappings"
    __table_args__ = {"schema": OPS_SCHEMA}

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_name: Mapped[str] = mapped_column(Text)
    upload_id: Mapped[str] = mapped_column(String(36))
    mapping: Mapped[dict[str, str]] = mapped_column(Json)
    suggested: Mapped[dict[str, Any]] = mapped_column(Json)
    default_state: Mapped[str] = mapped_column(String(2), default="CA")
    confirmed_by: Mapped[str] = mapped_column(String(128))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
