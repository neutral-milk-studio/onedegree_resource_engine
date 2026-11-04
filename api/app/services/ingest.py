"""Load a source into the HSDS store through the ingest transform.

The mapping and validation are ingest.pipeline.transform's. This module only keeps the payload as
received, folds the transform's flat objects into the store's documents (a phone goes inside its
service), and merges them with what is stored. received == stored + quarantined for every run,
counted per HSDS object as in the transform.
"""

from __future__ import annotations

import copy
import glob
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api.app.config import ROOT
from api.app.services.proposals import ChangeRequests
from api.app.services.store import TOP_LEVEL_TYPES, Conflict, HsdsStore, Resource, ValidationFailed
from ingest.pipeline.transform.adapter import MapperAdapter, flatten
from ingest.pipeline.transform.hsds import parents
from ingest.pipeline.transform.run import MAPPERS, split
from shared.db import Database, dumps, new_id, now_iso

# Objects stored inside their parent document, and the HSDS property that holds them.
EMBEDDED = {
    "phone": "phones",
    "schedule": "schedules",
    "address": "addresses",
    "language": "languages",
    "service_area": "service_areas",
    "accessibility": "accessibility",
    "attribute": "attributes",
    "required_document": "required_documents",
    "contact": "contacts",
    "cost_option": "cost_options",
    "funding": "funding",
    "url": "additional_urls",
}


@dataclass
class SourceConfig:
    name: str
    adapter: str
    location: str  # a glob of saved pages, relative to the repository root
    enabled: bool = True
    options: dict = field(default_factory=dict)


def load_source_configs(path: Path) -> list[SourceConfig]:
    return [SourceConfig(**item) for item in json.loads(Path(path).read_text())]


def _parent_of(kind: str, obj: dict, in_batch: dict[str, dict]) -> dict | None:
    if kind == "attribute":  # an attribute belongs to what it links to, not to its taxonomy term
        return in_batch.get(obj.get("link_id"))
    return next((in_batch[pid] for _, pid in parents(kind, obj) if pid in in_batch), None)


def assemble(objects: list[tuple[str, dict]]) -> tuple[list[Resource], list[tuple[str, dict, str]]]:
    """Fold flat (kind, object) pairs into documents. Returns the documents and any orphans."""
    copies = [(kind, copy.deepcopy(obj)) for kind, obj in objects]
    in_batch = {obj["id"]: obj for _, obj in copies}
    orphans = []
    for kind, obj in copies:
        if kind in TOP_LEVEL_TYPES:
            continue
        parent = _parent_of(kind, obj, in_batch)
        if kind not in EMBEDDED or parent is None:
            orphans.append((kind, obj, f"{kind} has no stored parent document"))
            continue
        parent.setdefault(EMBEDDED[kind], []).append(obj)

    resources = []
    for kind, obj in copies:
        if kind not in TOP_LEVEL_TYPES:
            continue
        org_id = obj["id"] if kind == "organization" else obj.get("organization_id")
        if kind == "service_at_location":
            org_id = in_batch.get(obj.get("service_id"), {}).get("organization_id")
        resources.append(Resource(kind, obj["id"], org_id, obj))
    return resources, orphans


class IngestService:
    def __init__(self, settings, db: Database, store: HsdsStore, blob, proposals: ChangeRequests):
        self.settings, self.db, self.store, self.blob = settings, db, store, blob
        self.proposals = proposals

    def run(
        self, cfg: SourceConfig, trigger: str = "scheduled", initiated_by: str = "scheduler"
    ) -> dict[str, Any]:
        run_id, started = new_id(), now_iso()
        self.db.run(
            "INSERT INTO import_run (id,source,trigger,initiated_by,started_at,status) "
            "VALUES (?,?,?,?,?,'running')",
            (run_id, cfg.name, trigger, initiated_by, started),
        )
        counts = dict.fromkeys(
            ("received", "stored", "quarantined", "clashes", "records", "shared_disagreements"), 0
        )
        seen: dict[str, str] = {}
        status, error = "completed", None
        try:
            adapter = MapperAdapter(MAPPERS[cfg.adapter])
            if adapter.name != cfg.name:  # ids derive from the source name, so these must agree
                raise ValueError(f"source {cfg.name!r} does not match mapper {adapter.name!r}")
            pattern = cfg.location if Path(cfg.location).is_absolute() else str(ROOT / cfg.location)
            for path in sorted(glob.glob(pattern)):
                raw = Path(path).read_bytes()
                key = self.blob.put(f"raw/{cfg.name}/{started}/{Path(path).name}", raw)
                self.db.run(
                    "INSERT INTO raw_payload (id,source,native_id,fetched_at,blob_key,run_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (new_id(), cfg.name, Path(path).name, started, key, run_id),
                )
                adapter.pages = [json.loads(raw)]
                for record in adapter.fetch():
                    self._one(adapter, cfg, record, started, run_id, counts, seen)
        except Exception as exc:  # a failed fetch: the counts so far stay truthful
            status, error = "failed", f"{type(exc).__name__}: {exc}"
        if status == "completed" and counts["received"] != counts["stored"] + counts["quarantined"]:
            status, error = "failed", "count mismatch: received != stored + quarantined"
        self.db.run(
            "UPDATE import_run SET finished_at=?, status=?, received=?, stored=?, quarantined=?, "
            "clashes=?, detail=? WHERE id=?",
            (
                now_iso(),
                status,
                counts["received"],
                counts["stored"],
                counts["quarantined"],
                counts["clashes"],
                dumps({"error": error, **counts}),
                run_id,
            ),
        )
        return {"run_id": run_id, "source": cfg.name, "status": status, "error": error, **counts}

    def _quarantine(self, run_id, source, native_id, kind, payload, reason) -> None:
        self.db.run(
            "INSERT INTO quarantine (id,run_id,source,native_id,reason,payload,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                new_id(),
                run_id,
                source,
                native_id,
                f"{kind}: {reason}",
                json.dumps(payload, ensure_ascii=False)[:200000],
                now_iso(),
            ),
        )

    def _one(self, adapter, cfg, record, fetched_at, run_id, counts, seen) -> None:
        counts["records"] += 1
        try:
            native_id = adapter.native_id(record)
            objects = flatten(adapter.map(record))
        except Exception as exc:  # an unmappable record is quarantined whole; the batch carries on
            counts["received"] += 1
            counts["quarantined"] += 1
            nid = str(record.get("id")) if isinstance(record, dict) else "?"
            self._quarantine(run_id, cfg.name, nid, "record", record, repr(exc))
            return

        counts["received"] += len(objects)
        good, bad = split(objects, lambda _kind, oid: self.store.get(oid) is not None)
        for kind, obj, errors in bad:
            counts["quarantined"] += 1
            self._quarantine(run_id, cfg.name, native_id, kind, obj, "; ".join(errors))
        resources, orphans = assemble(good)
        for kind, obj, reason in orphans:
            counts["quarantined"] += 1
            self._quarantine(run_id, cfg.name, native_id, kind, obj, reason)
        kept = len(good) - len(orphans)
        try:
            for res in resources:  # the merged document is judged, not only its flat parts
                if errors := self.store.validate_resource(res.type, res.data):
                    raise ValidationFailed(errors)
            clashes = 0
            with self.db.tx() as t:  # one source record is one atomic unit
                for res in resources:
                    digest = dumps(res.data)
                    if res.id in seen:  # shared objects: first version in a run wins
                        counts["shared_disagreements"] += seen[res.id] != digest
                        continue
                    seen[res.id] = digest
                    out = self.store.ingest_upsert(t, cfg.name, native_id, fetched_at, res, run_id)
                    for clash in out["clashes"]:
                        clashes += 1
                        self.proposals.create(
                            resource_id=res.id,
                            resource_type=res.type,
                            org_id=res.org_id,
                            kind="conflict",
                            proposed_by_type="source",
                            proposed_by=cfg.name,
                            patch={
                                clash["field"]: {
                                    "current": clash["current"],
                                    "proposed": clash["proposed"],
                                    "base": clash["base"],
                                }
                            },
                            title=(
                                "Source update clashes with a manual or AI change to "
                                f"'{clash['field']}'"
                            ),
                            tx=t,
                        )
            counts["stored"] += kept
            counts["clashes"] += clashes
        except (ValidationFailed, Conflict, KeyError, ValueError, TypeError) as exc:
            counts["quarantined"] += kept
            self._quarantine(run_id, cfg.name, native_id, "record", record, str(exc))
