"""Change requests: provider edits, AI-maintenance findings and ingest conflicts all wait here for
an admin decision. Applying one writes the HSDS record and history rows (R2.9, R2.10)."""

from __future__ import annotations

import json

from api.app.services.store import Conflict, HsdsStore
from shared.db import Database, Tx, dumps, loads, new_id, now_iso


class Stale(Exception):
    def __init__(self, fields: list[str]):
        super().__init__(f"record changed since the proposal for: {', '.join(fields)}")
        self.fields = fields


class ChangeRequests:
    def __init__(self, db: Database, store: HsdsStore):
        self.db, self.store = db, store

    def create(
        self,
        *,
        resource_id: str,
        resource_type: str,
        org_id: str | None,
        kind: str,
        proposed_by_type: str,
        proposed_by: str,
        patch: dict,
        evidence: list | None = None,
        category: str | None = None,
        title: str | None = None,
        tx: Tx | None = None,
    ) -> str:
        """patch = {field: {"current": ..., "proposed": ...}}. Skips exact duplicates of a pending request."""
        rid = new_id()
        sql_dup = "SELECT id, patch FROM change_request WHERE resource_id=? AND kind=? AND status='pending'"
        runner = tx if tx is not None else self.db
        for row in (tx.q if tx else self.db.q)(sql_dup, (resource_id, kind)):
            old = loads(row["patch"], {})
            if {f: v.get("proposed") for f, v in old.items()} == {
                f: v.get("proposed") for f, v in patch.items()
            }:
                return row["id"]
        (tx.run if tx else self.db.run)(
            "INSERT INTO change_request (id,resource_id,resource_type,org_id,kind,status,proposed_by_type,proposed_by,"
            "created_at,patch,evidence,category,title) VALUES (?,?,?,?,?,'pending',?,?,?,?,?,?,?)",
            (
                rid,
                resource_id,
                resource_type,
                org_id,
                kind,
                proposed_by_type,
                proposed_by,
                now_iso(),
                dumps(patch),
                dumps(evidence) if evidence else None,
                category,
                title,
            ),
        )
        return rid

    @staticmethod
    def _row(r: dict) -> dict:
        r = dict(r)
        r["patch"] = loads(r["patch"], {})
        r["evidence"] = loads(r.get("evidence"), [])
        return r

    def get(self, cid: str) -> dict | None:
        r = self.db.one("SELECT * FROM change_request WHERE id=?", (cid,))
        return self._row(r) if r else None

    def list(
        self, *, status=None, kind=None, org_id=None, resource_id=None, limit=200, offset=0
    ) -> list[dict]:
        where, p = ["1=1"], []
        for col, val in (
            ("status", status),
            ("kind", kind),
            ("org_id", org_id),
            ("resource_id", resource_id),
        ):
            if val:
                where.append(f"{col}=?")
                p.append(val)
        rows = self.db.q(
            f"SELECT * FROM change_request WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            p + [limit, offset],
        )
        return [self._row(r) for r in rows]

    def stale_fields(self, cr: dict, tx: Tx | None = None) -> list[str]:
        cur = self.store.get(cr["resource_id"], tx=tx)
        if not cur:
            return ["(record removed)"]
        out = []
        for f, v in cr["patch"].items():
            if "current" in v and json.dumps(cur["data"].get(f), sort_keys=True) != json.dumps(
                v.get("current"), sort_keys=True
            ):
                out.append(f)
        return out

    def _claim(self, t: Tx, cid: str, status: str, admin: str, note: str | None) -> dict:
        """Move a pending request to its final state. Only one concurrent decision can win."""
        won = t.run(
            "UPDATE change_request SET status=?, decided_by=?, decided_at=?, decision_note=? WHERE id=? AND status='pending'",
            (status, admin, now_iso(), note, cid),
        )
        row = t.one("SELECT * FROM change_request WHERE id=?", (cid,))
        if not row:
            raise KeyError(cid)
        if not won:
            raise Conflict("This change was already decided.")
        return self._row(row)

    def approve(
        self,
        cid: str,
        admin: str,
        note: str | None = None,
        overrides: dict | None = None,
        force: bool = False,
    ) -> dict:
        """Apply a proposal. The decision, the record write and its history share one transaction."""
        with self.db.tx() as t:
            cr = self._claim(t, cid, "applied", admin, note)
            stale = self.stale_fields(cr, tx=t)
            if stale and not force:
                raise Stale(stale)
            patch = {f: (overrides or {}).get(f, v.get("proposed")) for f, v in cr["patch"].items()}
            return self.store.apply_patch(
                cr["resource_id"],
                patch,
                cr["proposed_by_type"],
                cr["proposed_by"],
                request_id=cid,
                note=f"approved by {admin}" + (f": {note}" if note else ""),
                tx=t,
            )

    def reject(self, cid: str, admin: str, note: str | None = None) -> None:
        with self.db.tx() as t:
            self._claim(t, cid, "rejected", admin, note)
