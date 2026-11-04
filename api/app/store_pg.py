"""PostgreSQL HsdsStore implementation (SQLAlchemy; SQLite works too, for unit tests).

Implements the ``contracts.store.HsdsStore`` read surface plus the write helpers the
ingest pipeline needs. Every served record round-trips to valid HSDS v3.2.3.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from contracts.store import IngestCounts, Page, ServiceFilter
from ingest.transformer.assemble import LINK_FIELDS, assemble_service, key_columns, served
from shared.models import AuditEntry, HsdsObject, IdMap, IngestRun, QuarantineEntry, Service

EXT = "x_resource_engine"


def content_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class DbIndex:
    """ObjectIndex over the hsds.objects table (for assembling stored services)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, kind: str, object_id: str) -> dict | None:
        row = self.session.get(HsdsObject, object_id)
        return row.body if row is not None and row.kind == kind else None

    def children(self, kind: str, field: str, parent_id: str) -> list[dict]:
        column = getattr(HsdsObject, field)
        rows = self.session.scalars(
            select(HsdsObject).where(HsdsObject.kind == kind, column == parent_id)
        )
        return [r.body for r in rows]


class PgHsdsStore:
    """Concrete HsdsStore (satisfies contracts.store.HsdsStore's read surface)."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session, session.begin():
            yield session

    # -- writes used by the ingest pipeline ----------------------------------------------

    def exists(self, session: Session, kind: str, object_id: str) -> bool:
        row = session.get(HsdsObject, object_id)
        return row is not None and row.kind == kind

    def keep(self, session: Session, kind: str, source: str, record_native_id: str, obj: dict):
        """Upsert one flat HSDS object and its id_map row. Idempotent by id."""
        native = str(((obj.get(EXT) or {}).get("source") or {}).get("record_id", record_native_id))
        links = {field: obj.get(field) for field in LINK_FIELDS}
        row = session.get(HsdsObject, obj["id"])
        digest = content_hash(obj)
        if row is None:
            session.add(
                HsdsObject(
                    id=obj["id"],
                    kind=kind,
                    source=source,
                    record_native_id=record_native_id,
                    body=obj,
                    content_hash=digest,
                    **links,
                )
            )
            session.add(
                IdMap(
                    hsds_id=obj["id"],
                    kind=kind,
                    source=source,
                    native_id=native,
                    record_native_id=record_native_id,
                )
            )
        elif row.content_hash != digest:
            row.body, row.content_hash, row.record_native_id = obj, digest, record_native_id
            for field, value in links.items():
                setattr(row, field, value)
        session.flush()

    def save_service(self, session: Session, source: str, record_native_id: str, doc: dict) -> bool:
        """Upsert the nested service row. Returns True if it was created or changed."""
        digest = content_hash(doc)
        native = str(((doc.get(EXT) or {}).get("source") or {}).get("record_id", record_native_id))
        row = session.get(Service, doc["id"])
        columns = key_columns(doc)
        if row is None:
            session.add(
                Service(
                    id=doc["id"],
                    source=source,
                    native_id=native,
                    record_native_id=record_native_id,
                    document=doc,
                    content_hash=digest,
                    version=1,
                    **columns,
                )
            )
            return True
        if row.content_hash == digest:
            return False
        row.document, row.content_hash, row.version = doc, digest, row.version + 1
        for field, value in columns.items():
            setattr(row, field, value)
        return True

    def assemble(self, session: Session, service_id: str) -> dict | None:
        index = DbIndex(session)
        flat = index.get("service", service_id)
        return assemble_service(index, flat) if flat else None

    def add_quarantine(self, session: Session, **fields) -> None:
        session.add(QuarantineEntry(**fields))

    def audit(self, session: Session, **fields) -> None:
        session.add(AuditEntry(**fields))

    # -- contracts.store.HsdsStore reads -------------------------------------------------

    def get_service(self, service_id: str, source: str | None = None) -> dict[str, Any] | None:
        with self.session() as session:
            row = session.get(Service, service_id)
            if row is None or (source and row.source != source):
                return None
            return served(row.document)

    def list_services(self, filt: ServiceFilter, page: int, per_page: int) -> Page:
        query = select(Service)
        if filt.organization_id:
            query = query.where(Service.organization_id == filt.organization_id)
        if filt.source:
            query = query.where(Service.source == filt.source)
        if filt.modified_after:
            query = query.where(Service.updated_at > datetime.fromisoformat(filt.modified_after))
        with self.session() as session:
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.scalars(
                query.order_by(Service.name, Service.id)
                .offset((page - 1) * per_page)
                .limit(per_page)
            ).all()
            contents = [served(r.document, include_metadata=False) for r in rows]
        total_pages = math.ceil(total / per_page) if total else 0
        return Page(
            total_items=total,
            total_pages=total_pages,
            page_number=page,
            size=per_page,
            first_page=page == 1,
            last_page=page >= total_pages,
            empty=not contents,
            contents=contents,
        )

    def counts(self, source: str) -> IngestCounts:
        """Counts from the most recent finished run for ``source``."""
        with self.session() as session:
            run = session.scalars(
                select(IngestRun)
                .where(IngestRun.source == source, IngestRun.status == "succeeded")
                .order_by(IngestRun.started_at.desc())
            ).first()
            if run is None:
                return IngestCounts(0, 0, 0)
            return IngestCounts(run.received, run.stored, run.quarantined)

    def list_runs(self, limit: int = 50) -> list[IngestRun]:
        with self.session() as session:
            return list(
                session.scalars(
                    select(IngestRun).order_by(IngestRun.started_at.desc()).limit(limit)
                )
            )

    def get_run(self, run_id: str) -> IngestRun | None:
        with self.session() as session:
            return session.get(IngestRun, run_id)

    def quarantined(self, run_id: str) -> list[QuarantineEntry]:
        with self.session() as session:
            return list(
                session.scalars(
                    select(QuarantineEntry)
                    .where(QuarantineEntry.run_id == run_id)
                    .order_by(QuarantineEntry.created_at)
                )
            )

    # Contract methods filled in by later steps (review/apply in step 3).
    def upsert_record(self, hsds_objects: dict[str, Any], source: str, native_id: str) -> str:
        raise NotImplementedError("use ingest.pipeline.run.run_ingest, which validates first")

    def write_metadata(self, *args, **kwargs) -> None:
        raise NotImplementedError("added with the review/apply flow (step 3)")

    def quarantine(self, source: str, native_id: str, raw_ref: str, reason: str) -> None:
        raise NotImplementedError("the pipeline quarantines inside its run transaction")
