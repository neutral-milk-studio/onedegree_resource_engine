"""On-demand ingest: fetch -> save raw -> map -> validate -> store or quarantine.

Ported from ``transform/run.py`` (feature/ingest-transform): :func:`split` is unchanged.
New here: Postgres storage, the raw store, record-level counts, and validation of each
assembled nested service in its served form (with UTC offsets), so nothing invalid is
ever served.

Counts are reported at two levels and both reconcile:

- records (what the brief and dashboard show): ``received == stored + quarantined``.
  A record is quarantined when it can't be mapped or its organization/service is invalid.
- objects: ``objects_total == objects_kept + objects_quarantined``. A record can be stored
  while one child object (e.g. an address with no city) is held back with its reason.

Usage::

    python -m ingest.pipeline.run odrs
    python -m ingest.pipeline.run --all
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from api.app.store_pg import PgHsdsStore
from ingest.adapters.base import ModuleAdapter
from ingest.adapters.registry import get_adapter, source_names
from ingest.raw_store.local import LocalRawStore
from ingest.transformer.assemble import MemoryIndex, assemble_service, served
from ingest.transformer.hsds import parents, validate
from shared.hsds_validation import find_issues
from shared.models import IngestRun

log = logging.getLogger(__name__)

ROOT_KINDS = frozenset({"organization", "service"})


def split(objects: list[tuple[str, dict]], exists=lambda kind, id: False):
    """Validate each object and check its parents; return (good, bad).

    A child is bad if its parent failed (the failure cascades down) or if its parent is
    neither in this batch nor already stored. Checking this here keeps the output valid
    HSDS whatever database it ends up in, including ones that don't enforce foreign keys.
    """
    bad: dict[str, tuple[str, dict, list[str]]] = {}
    for kind, obj in objects:
        if errors := validate(kind, obj):
            bad[obj.get("id") or f"no-id-{len(bad)}"] = (kind, obj, errors)
    in_batch = {obj.get("id") for _, obj in objects}

    changed = True
    while changed:
        changed = False
        for kind, obj in objects:
            if obj.get("id") in bad:
                continue
            for parent_kind, parent_id in parents(kind, obj):
                if parent_id in bad:
                    reason = f"(root): parent {parent_kind} {parent_id} was quarantined"
                elif parent_id not in in_batch and not exists(parent_kind, parent_id):
                    reason = f"(root): parent {parent_kind} {parent_id} does not exist"
                else:
                    continue
                bad[obj["id"]] = (kind, obj, [reason])
                changed = True
                break

    good = [(k, o) for k, o in objects if o.get("id") not in bad]
    return good, list(bad.values())


def validate_record(objects: list[tuple[str, dict]], exists=lambda kind, id: False):
    """:func:`split`, plus served-form validation of every nested service.

    Returns ``(good, bad, docs)``: objects to keep, (kind, obj, reasons) to quarantine, and
    the nested service documents by id. A service whose nested served form is invalid is
    quarantined along with everything that hangs off it.
    """
    failed: dict[str, tuple[str, dict, list[str]]] = {}
    while True:
        remaining = [(k, o) for k, o in objects if o.get("id") not in failed]
        good, bad = split(remaining, lambda k, i: i not in failed and exists(k, i))
        index = MemoryIndex(good)
        docs: dict[str, dict] = {}
        newly_failed = False
        for kind, obj in good:
            if kind != "service":
                continue
            doc = assemble_service(index, obj)
            issues = find_issues(served(doc), "service")
            if issues:
                failed[obj["id"]] = (kind, obj, [f"served HSDS service: {i}" for i in issues])
                newly_failed = True
            else:
                docs[obj["id"]] = doc
        if not newly_failed:
            break
    fixed_bad = []
    for kind, obj, reasons in bad:
        reasons = [
            r.replace("does not exist", "was quarantined") if any(fid in r for fid in failed) else r
            for r in reasons
        ]
        fixed_bad.append((kind, obj, reasons))
    return good, fixed_bad + list(failed.values()), docs


@dataclass
class IngestReport:
    run_id: str
    source: str
    status: str = "running"
    received: int = 0
    stored: int = 0
    quarantined: int = 0
    services_stored: int = 0
    services_changed: int = 0
    objects_total: int = 0
    objects_kept: int = 0
    objects_quarantined: int = 0
    elapsed_ms: int = 0
    raw_refs: list[str] = field(default_factory=list)
    quarantine_reasons: list[dict] = field(default_factory=list)

    def reconciled(self) -> bool:
        return (
            self.received == self.stored + self.quarantined
            and self.objects_total == self.objects_kept + self.objects_quarantined
        )


def _native_id(adapter: ModuleAdapter, record: dict, seq: int) -> str:
    try:
        return adapter.native_id(record)
    except Exception:  # noqa: BLE001 - a record with no id is still counted and quarantined
        return f"record#{seq}"


def run_ingest(
    adapter: ModuleAdapter,
    store: PgHsdsStore,
    raw_store: LocalRawStore | None = None,
    triggered_by: str = "system",
) -> IngestReport:
    """Run one ingest for ``adapter`` and return reconciled counts. Re-runs update in place."""
    raw_store = raw_store or LocalRawStore()
    source = adapter.name
    started = time.monotonic()
    fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with store.session() as session:
        run = IngestRun(source=source, triggered_by=triggered_by, status="running")
        session.add(run)
    report = IngestReport(run_id=run.id, source=source)
    log.info("ingest started", extra={"source": source, "run_id": run.id})

    def hold(session, native_id, level, kind, obj, reasons, raw_ref):
        store.add_quarantine(
            session,
            run_id=run.id,
            source=source,
            native_id=native_id,
            level=level,
            kind=kind,
            object_id=obj.get("id") if isinstance(obj, dict) else None,
            reasons=reasons,
            payload=obj,
            raw_ref=raw_ref,
        )
        report.quarantine_reasons.append(
            {"native_id": native_id, "level": level, "kind": kind, "reasons": reasons}
        )
        log.info(
            "quarantined",
            extra={"source": source, "native_id": native_id, "kind": kind, "reasons": reasons},
        )

    try:
        with store.session() as session:
            for page in adapter.fetch_pages():
                # Raw first, exactly as received, before any parsing.
                ref = raw_store.write(source, fetched_at, page.name, page.content)
                report.raw_refs.append(ref)
                try:
                    parsed = page.parse()
                except json.JSONDecodeError as exc:
                    raise ValueError(f"page {page.name} is not valid JSON: {exc}") from exc
                for record in adapter.records(parsed):
                    report.received += 1
                    native = _native_id(adapter, record, report.received)
                    raw_ref = f"{ref}#{native}"
                    try:
                        objects = adapter.map_ordered(record)
                    except Exception as exc:  # noqa: BLE001 - unmappable record: quarantine it
                        hold(session, native, "record", "record", record, [repr(exc)], raw_ref)
                        report.quarantined += 1
                        continue

                    savepoint = session.begin_nested()
                    try:
                        good, bad, docs = validate_record(
                            objects, lambda k, i: store.exists(session, k, i)
                        )
                        for kind, obj in good:
                            store.keep(session, kind, source, native, obj)
                        for doc in docs.values():
                            report.services_changed += store.save_service(
                                session, source, native, doc
                            )
                        savepoint.commit()
                    except Exception as exc:  # noqa: BLE001 - storage error: hold the record
                        savepoint.rollback()
                        log.exception("store failed", extra={"source": source, "native": native})
                        hold(session, native, "record", "record", record, [repr(exc)], raw_ref)
                        report.quarantined += 1
                        report.objects_total += len(objects)
                        report.objects_quarantined += len(objects)
                        continue

                    root_failed = any(kind in ROOT_KINDS for kind, _, _ in bad)
                    for kind, obj, reasons in bad:
                        level = "record" if kind in ROOT_KINDS else "object"
                        hold(session, native, level, kind, obj, reasons, raw_ref)
                    report.objects_total += len(objects)
                    report.objects_kept += len(good)
                    report.objects_quarantined += len(bad)
                    report.services_stored += len(docs)
                    if root_failed:
                        report.quarantined += 1
                    else:
                        report.stored += 1
    except Exception as exc:
        report.status = "failed"
        report.elapsed_ms = int((time.monotonic() - started) * 1000)
        with store.session() as session:
            row = session.get(IngestRun, run.id)
            row.status, row.error = "failed", str(exc)
            row.finished_at, row.elapsed_ms = datetime.now(UTC), report.elapsed_ms
        log.exception("ingest failed", extra={"source": source, "run_id": run.id})
        raise

    if not report.reconciled():  # never expected; fail loudly rather than misreport
        raise AssertionError(f"ingest counts do not reconcile: {asdict(report)}")

    report.status = "succeeded"
    report.elapsed_ms = int((time.monotonic() - started) * 1000)
    with store.session() as session:
        row = session.get(IngestRun, run.id)
        for name in (
            "status",
            "received",
            "stored",
            "quarantined",
            "services_stored",
            "objects_total",
            "objects_kept",
            "objects_quarantined",
            "elapsed_ms",
            "raw_refs",
        ):
            setattr(row, name, getattr(report, name))
        row.finished_at = datetime.now(UTC)
        store.audit(
            session,
            actor=triggered_by,
            action="ingest_run",
            change_source="ingest",
            entity_type="source",
            entity_id=source,
            new_value={
                "received": report.received,
                "stored": report.stored,
                "quarantined": report.quarantined,
                "services_changed": report.services_changed,
            },
            summary=(
                f"Ingested {source}: {report.received} received, {report.stored} stored, "
                f"{report.quarantined} quarantined"
            ),
        )
    log.info(
        "ingest finished",
        extra={k: v for k, v in asdict(report).items() if k != "quarantine_reasons"},
    )
    return report


def main() -> None:
    from shared.db import get_engine, init_db
    from shared.logging_setup import configure_logging

    configure_logging()
    parser = argparse.ArgumentParser(description="Ingest configured sources into the HSDS store.")
    parser.add_argument("sources", nargs="*", help=f"source names: {source_names()}")
    parser.add_argument("--all", action="store_true", help="ingest every configured source")
    args = parser.parse_args()
    names = source_names() if args.all else args.sources
    if not names:
        parser.error("name at least one source, or pass --all")

    engine = get_engine()
    init_db(engine, retries=10)
    store = PgHsdsStore(engine)
    for name in names:
        report = run_ingest(get_adapter(name), store, triggered_by="cli")
        summary = {k: v for k, v in asdict(report).items() if k != "quarantine_reasons"}
        print(json.dumps(summary, indent=2))
        for q in report.quarantine_reasons:
            print(
                f"QUARANTINED {q['level']} {q['kind']} (record {q['native_id']}): "
                + "; ".join(q["reasons"])
            )


if __name__ == "__main__":
    main()
