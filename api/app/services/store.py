"""HSDS store: validated records, field-level change history, source provenance and
three-way reconciliation of repeated source updates (R4.1-R4.3).

Every record is stored as one HSDS JSON document (children such as phones/schedules are
embedded, each carrying its own id) in the generic `resource` table. Storage is internal;
everything that leaves the system goes through app.hsds.nest and is HSDS JSON (rule 4.2).
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from shared.db import Database, Tx, dumps, loads, new_id, now_iso

MISSING = object()
PROTECTED = {"id", "x_resource_engine"}
TOP_LEVEL_TYPES = (
    "organization",
    "service",
    "location",
    "service_at_location",
    "taxonomy",
    "taxonomy_term",
)


@dataclass
class Resource:
    """One top-level HSDS document as stored in the resource table."""

    type: str
    id: str
    org_id: str | None
    data: dict


class Conflict(Exception):
    """The record changed after it was read. Nothing was saved."""


class ValidationFailed(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors[:3]))
        self.errors = errors


class HsdsStore:
    def __init__(self, db: Database, validator):
        self.db, self.validator = db, validator

    @contextmanager
    def _tx(self, tx: Tx | None) -> Iterator[Tx]:
        if tx is not None:
            yield tx
        else:
            with self.db.tx() as t:
                yield t

    # ---- reads ------------------------------------------------------------------------------
    @staticmethod
    def _row(r: dict) -> dict:
        return {
            "id": r["id"],
            "type": r["type"],
            "org_id": r["org_id"],
            "source": r["source"],
            "status": r["status"],
            "version": r["version"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "data": loads(r["data"], {}),
        }

    def get(self, rid: str, tx: Tx | None = None) -> dict | None:
        with self._tx(tx) as t:
            r = t.one("SELECT * FROM resource WHERE id=? AND deleted=0", (rid,))
        return self._row(r) if r else None

    def get_many(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            rows = self.db.q(
                f"SELECT * FROM resource WHERE deleted=0 AND id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
            out.update({r["id"]: self._row(r) for r in rows})
        return out

    def list(
        self,
        rtype: str,
        *,
        org_id=None,
        source=None,
        status=None,
        modified_after=None,
        term_id=None,
        taxonomy_id=None,
        ids: list[str] | None = None,
        limit=50,
        offset=0,
        text_filter: str | None = None,
    ) -> tuple[list[dict], int]:
        where, p = ["type=?", "deleted=0"], [rtype]
        if org_id:
            where.append("org_id=?")
            p.append(org_id)
        if source:
            where.append("source=?")
            p.append(source)
        if status:
            where.append("status=?")
            p.append(status)
        if modified_after:
            where.append("updated_at>?")
            p.append(modified_after)
        if term_id:
            where.append("data LIKE ?")
            p.append(f'%"taxonomy_term_id": "{term_id}"%')
        if ids is not None:
            if not ids:
                return [], 0
            where.append(f"id IN ({','.join('?' * len(ids))})")
            p.extend(ids)
        if text_filter:
            where.append("LOWER(data) LIKE ?")
            p.append(f"%{text_filter.lower()}%")
        w = " AND ".join(where)
        if taxonomy_id:  # filter in Python: services having any term of this taxonomy
            terms = {
                r["id"]
                for r in self.db.q(
                    "SELECT id FROM resource WHERE type='taxonomy_term' AND deleted=0 AND data LIKE ?",
                    (f'%"taxonomy_id": "{taxonomy_id}"%',),
                )
            }
            rows = [
                self._row(r) for r in self.db.q(f"SELECT * FROM resource WHERE {w} ORDER BY id", p)
            ]
            rows = [
                r
                for r in rows
                if any(a.get("taxonomy_term_id") in terms for a in r["data"].get("attributes", []))
            ]
            return rows[offset : offset + limit], len(rows)
        total = self.db.one(f"SELECT COUNT(*) AS n FROM resource WHERE {w}", p)["n"]
        rows = self.db.q(
            f"SELECT * FROM resource WHERE {w} ORDER BY id LIMIT ? OFFSET ?", p + [limit, offset]
        )
        return [self._row(r) for r in rows], total

    def all_of_type(self, rtype: str) -> list[dict]:
        return [
            self._row(r)
            for r in self.db.q(
                "SELECT * FROM resource WHERE type=? AND deleted=0 ORDER BY id", (rtype,)
            )
        ]

    def counts(self) -> dict[str, int]:
        return {
            r["type"]: r["n"]
            for r in self.db.q(
                "SELECT type, COUNT(*) AS n FROM resource WHERE deleted=0 GROUP BY type"
            )
        }

    def history(self, rid: str, limit: int = 200) -> list[dict]:
        rows = self.db.q(
            "SELECT * FROM change_history WHERE resource_id=? ORDER BY last_action_date DESC, id LIMIT ?",
            (rid, limit),
        )
        return rows

    # ---- writes -----------------------------------------------------------------------------
    def _history(
        self,
        t: Tx,
        rid: str,
        rtype: str,
        field: str,
        prev: Any,
        new: Any,
        action: str,
        actor_type: str,
        who: str,
        request_id: str | None = None,
        note: str | None = None,
    ) -> None:
        enc = lambda v: None if v is MISSING or v is None else json.dumps(v, ensure_ascii=False)
        t.run(
            "INSERT INTO change_history (id,resource_id,resource_type,field_name,previous_value,replacement_value,"
            "last_action_type,last_action_date,actor_type,updated_by,request_id,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id(),
                rid,
                rtype,
                field,
                enc(prev),
                enc(new),
                action,
                now_iso(precise=True),
                actor_type,
                who,
                request_id,
                note,
            ),
        )

    def _write(
        self,
        t: Tx,
        res_type: str,
        rid: str,
        org_id,
        source,
        data: dict,
        version: int,
        created_at: str | None,
    ):
        status = data.get("status") if res_type == "service" else None
        n = t.run(
            "INSERT INTO resource (id,type,org_id,source,status,data,version,created_at,updated_at,deleted) "
            "VALUES (?,?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET org_id=excluded.org_id, status=excluded.status, "
            "data=excluded.data, version=excluded.version, updated_at=excluded.updated_at, deleted=0 "
            "WHERE resource.version=?",
            (
                rid,
                res_type,
                org_id,
                source,
                status,
                dumps(data),
                version,
                created_at or now_iso(),
                now_iso(),
                version - 1,
            ),
        )
        if not n:  # compare-and-swap: someone else wrote this record since it was read
            raise Conflict(f"Record {rid} changed since it was read. Reload and try again.")

    def validate_resource(self, rtype: str, data: dict) -> list[str]:
        return self.validator.errors(rtype, data) if rtype in TOP_LEVEL_TYPES else []

    def ingest_upsert(
        self, t: Tx, source: str, native_id: str, fetched_at: str, res: Resource, run_id: str | None
    ) -> dict:
        """Create or reconcile one mapped resource. Returns {'action', 'changes', 'clashes'}."""
        existing = t.one("SELECT * FROM resource WHERE id=?", (res.id,))
        snap_row = t.one(
            "SELECT snapshot FROM resource_source WHERE resource_id=? AND source=?",
            (res.id, source),
        )
        theirs = res.data
        base = loads(snap_row["snapshot"]) if snap_row and snap_row["snapshot"] else None
        outcome = {"action": "unchanged", "changes": [], "clashes": []}
        if existing is None or existing["deleted"]:
            self._write(
                t,
                res.type,
                res.id,
                res.org_id,
                source,
                theirs,
                existing["version"] + 1 if existing else 1,
                None,
            )
            self._history(
                t, res.id, res.type, "*", None, "created", "create", "source", source, run_id
            )
            outcome["action"] = "created"
        else:
            ours = loads(existing["data"], {})
            merged = dict(ours)
            fields = (set(theirs) | set(base or {}) | set(ours)) - PROTECTED
            for f in sorted(fields):
                tv, bv, ov = (
                    theirs.get(f, MISSING),
                    (base or {}).get(f, MISSING),
                    ours.get(f, MISSING),
                )
                if base is None:  # no snapshot: only fill gaps, never overwrite
                    if ov is MISSING and tv is not MISSING:
                        merged[f] = tv
                        outcome["changes"].append((f, MISSING, tv))
                    continue
                if tv == bv or tv == ov:
                    continue  # source unchanged, or already equal
                if ov == bv:  # only the source changed -> apply
                    if tv is MISSING:
                        merged.pop(f, None)
                    else:
                        merged[f] = tv
                    outcome["changes"].append((f, ov, tv))
                else:  # both changed differently -> clash
                    outcome["clashes"].append(
                        {
                            "field": f,
                            "current": None if ov is MISSING else ov,
                            "proposed": None if tv is MISSING else tv,
                            "base": None if bv is MISSING else bv,
                        }
                    )
            # keep system extension, refresh source info, drop resolved flags
            x = dict(ours.get("x_resource_engine") or {})
            src_x = theirs.get("x_resource_engine") or {}
            x["source"] = src_x.get("source", x.get("source"))
            flags = set(x.get("flags", [])) | set(src_x.get("flags", []))
            if not str(merged.get("description", "")).endswith("(description not available)"):
                flags.discard("description_missing")
            if flags:
                x["flags"] = sorted(flags)
            else:
                x.pop("flags", None)
            merged["x_resource_engine"] = x
            if outcome["changes"]:
                errs = self.validate_resource(res.type, merged)
                if errs:
                    raise ValidationFailed(errs)
                self._write(
                    t,
                    res.type,
                    res.id,
                    res.org_id,
                    existing["source"],
                    merged,
                    existing["version"] + 1,
                    existing["created_at"],
                )
                for f, prev, new in outcome["changes"]:
                    self._history(
                        t, res.id, res.type, f, prev, new, "update", "source", source, run_id
                    )
                outcome["action"] = "updated"
        t.run(
            "INSERT INTO resource_source (resource_id,source,native_id,fetched_at,snapshot) VALUES (?,?,?,?,?) "
            "ON CONFLICT(resource_id,source) DO UPDATE SET native_id=excluded.native_id, fetched_at=excluded.fetched_at, "
            "snapshot=excluded.snapshot",
            (res.id, source, native_id, fetched_at, dumps(theirs)),
        )
        return outcome

    @staticmethod
    def _apply_to(cur: dict, patch: dict) -> tuple[dict, list[tuple]]:
        new, changed = dict(cur), []
        for f, v in patch.items():
            empty = v in (None, "", [], {})
            if empty and f in new:
                changed.append((f, new.pop(f), MISSING))
            elif not empty and new.get(f, MISSING) != v:
                changed.append((f, new.get(f, MISSING), v))
                new[f] = v
        return new, changed

    def validate_patch(self, rid: str, patch: dict) -> list[str]:
        """Dry run: errors the patch would cause (used when a provider submits a change)."""
        cur = self.get(rid)
        if not cur:
            return [f"resource {rid} not found"]
        bad = PROTECTED & set(patch)
        if bad:
            return [f"fields {sorted(bad)} cannot be edited"]
        new, _ = self._apply_to(cur["data"], patch)
        return self.validate_resource(cur["type"], new)

    def apply_patch(
        self,
        rid: str,
        patch: dict,
        actor_type: str,
        who: str,
        request_id: str | None = None,
        note: str | None = None,
        tx: Tx | None = None,
        expected_version: int | None = None,
    ) -> dict:
        """Apply a field patch: HSDS schema validation on save, one history row per changed field."""
        with self._tx(tx) as t:
            row = t.one("SELECT * FROM resource WHERE id=? AND deleted=0", (rid,))
            if not row:
                raise KeyError(rid)
            if expected_version is not None and row["version"] != expected_version:
                raise Conflict(f"Record {rid} changed since it was read. Reload and try again.")
            bad = PROTECTED & set(patch)
            if bad:
                raise ValidationFailed([f"fields {sorted(bad)} cannot be edited"])
            new, changed = self._apply_to(loads(row["data"], {}), patch)
            if not changed:
                return {"version": row["version"], "changed": 0}
            errs = self.validate_resource(row["type"], new)
            if errs:
                raise ValidationFailed(errs)
            self._write(
                t,
                row["type"],
                rid,
                row["org_id"],
                row["source"],
                new,
                row["version"] + 1,
                row["created_at"],
            )
            for f, prev, nv in changed:
                self._history(
                    t, rid, row["type"], f, prev, nv, "update", actor_type, who, request_id, note
                )
            return {"version": row["version"] + 1, "changed": len(changed)}
