"""Nested HSDS views for the API. Internal storage is flat; everything served is HSDS JSON (rule 4.2)."""

from __future__ import annotations

from api.app.services.store import HsdsStore
from shared.db import loads


def strip_ext(data: dict, keep: bool = False) -> dict:
    return dict(data) if keep else {k: v for k, v in data.items() if not k.startswith("x_")}


class Views:
    """Per-request view builder with page-level prefetch to avoid N+1 queries."""

    def __init__(self, store: HsdsStore):
        self.store, self.db = store, store.db
        self.orgs: dict[str, dict] = {}
        self.locs: dict[str, dict] = {}
        self.terms: dict[str, dict] = {}
        self.sals: dict[str, list[dict]] = {}

    def prefetch(self, service_rows: list[dict]) -> None:
        org_ids = list({r["org_id"] for r in service_rows if r["org_id"]})
        if not org_ids:
            return
        self.orgs.update(self.store.get_many(org_ids))
        for i in range(0, len(org_ids), 500):
            chunk = org_ids[i : i + 500]
            for r in self.db.q(
                "SELECT * FROM resource WHERE type='service_at_location' AND deleted=0 AND org_id IN (%s)"
                % ",".join("?" * len(chunk)),
                chunk,
            ):
                row = self.store._row(r)
                self.sals.setdefault(row["data"].get("service_id", ""), []).append(row)
        loc_ids = {
            s["data"]["location_id"]
            for rows in self.sals.values()
            for s in rows
            if s["data"].get("location_id")
        }
        self.locs.update(self.store.get_many(list(loc_ids - set(self.locs))))
        term_ids = {
            a["taxonomy_term_id"]
            for r in service_rows
            for a in r["data"].get("attributes", [])
            if a.get("taxonomy_term_id")
        }
        self._load_terms(term_ids)

    def _load_terms(self, ids: set[str]) -> None:
        todo = ids - set(self.terms)
        while todo:
            self.terms.update(self.store.get_many(list(todo)))
            todo = {
                t["data"].get("parent_id")
                for t in self.terms.values()
                if t["data"].get("parent_id")
            } - set(self.terms)

    def term_path(self, tid: str) -> list[str]:
        path, seen = [], set()
        while tid and tid in self.terms and tid not in seen:
            seen.add(tid)
            t = self.terms[tid]["data"]
            path.append(t["name"])
            tid = t.get("parent_id")
        return list(reversed(path))

    # ---- views -----------------------------------------------------------------------------------
    def service(self, row: dict, *, minimal: bool = False, ext: bool = False) -> dict:
        d = strip_ext(row["data"], ext)
        if minimal:
            return {
                k: d[k]
                for k in ("id", "name", "description", "status", "organization_id")
                if k in d
            }
        org = self.orgs.get(row["org_id"])
        if org:
            d["organization"] = {
                k: v
                for k, v in strip_ext(org["data"], ext).items()
                if k in ("id", "name", "description", "website", "email")
            }
        sals = []
        for s in self.sals.get(row["id"], []):
            sd = strip_ext(s["data"], ext)
            loc = self.locs.get(sd.get("location_id", ""))
            if loc:
                sd["location"] = strip_ext(loc["data"], ext)
            sals.append(sd)
        if sals:
            d["service_at_locations"] = sals
        attrs = []
        for a in d.get("attributes", []):
            a = dict(a)
            t = self.terms.get(a.get("taxonomy_term_id", ""))
            if t:
                a["taxonomy_term"] = {**strip_ext(t["data"], ext), "path": self.term_path(t["id"])}
            attrs.append(a)
        if attrs:
            d["attributes"] = attrs
        return d

    def organization(self, row: dict, *, minimal: bool = False, ext: bool = False) -> dict:
        d = strip_ext(row["data"], ext)
        if minimal:
            return {k: d[k] for k in ("id", "name", "description") if k in d}
        services, _ = self.store.list("service", org_id=row["id"], limit=1000)
        d["services"] = [
            {k: s["data"][k] for k in ("id", "name", "status") if k in s["data"]} for s in services
        ]
        return d

    def location(self, row: dict, *, ext: bool = False) -> dict:
        return strip_ext(row["data"], ext)

    def taxonomy_term(self, row: dict, *, ext: bool = False) -> dict:
        self._load_terms({row["id"]})
        d = strip_ext(row["data"], ext)
        d["path"] = self.term_path(row["id"])
        return d

    def service_at_location(self, row: dict, *, ext: bool = False) -> dict:
        d = strip_ext(row["data"], ext)
        loc = self.store.get(d.get("location_id", ""))
        svc = self.store.get(d.get("service_id", ""))
        if loc:
            d["location"] = strip_ext(loc["data"], ext)
        if svc:
            d["service"] = {k: svc["data"][k] for k in ("id", "name", "status") if k in svc["data"]}
        return d
