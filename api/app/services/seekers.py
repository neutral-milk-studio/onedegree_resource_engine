"""Seeker profiles, help requests and provider matching."""

from __future__ import annotations

from shared.db import Database, dumps, loads, new_id, now_iso

STATUSES = ("open", "accepted", "declined", "closed")


class SeekerService:
    def __init__(self, db: Database, search, store):
        self.db, self.search, self.store = db, search, store

    # ---- profile -----------------------------------------------------------------------------------
    def get_profile(self, user_id: str) -> dict:
        r = self.db.one("SELECT data FROM seeker_profile WHERE user_id=?", (user_id,))
        return loads(r["data"], {}) if r else {}

    def save_profile(self, user_id: str, data: dict) -> dict:
        allowed = {
            "phone",
            "zip",
            "household_size",
            "language",
            "needs",
            "contact_preference",
            "share_with_providers",
            "notes",
            "age_group",
        }
        clean = {k: v for k, v in data.items() if k in allowed}
        cur = {**self.get_profile(user_id), **clean}
        self.db.run(
            "INSERT INTO seeker_profile (user_id,data,updated_at) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (user_id, dumps(cur), now_iso()),
        )
        return cur

    # ---- requests ----------------------------------------------------------------------------------
    def create_request(
        self,
        seeker_id: str,
        need_text: str,
        service_id: str | None = None,
        score: float | None = None,
    ) -> dict:
        prof = self.get_profile(seeker_id)
        org_id = None
        if service_id:
            svc = self.store.get(service_id)
            org_id = svc["org_id"] if svc else None
        rid = new_id()
        self.db.run(
            "INSERT INTO help_request (id,seeker_id,need_text,status,service_id,org_id,match_score,share_consent,created_at,updated_at) "
            "VALUES (?,?,?,'open',?,?,?,?,?,?)",
            (
                rid,
                seeker_id,
                need_text,
                service_id,
                org_id,
                score,
                1 if prof.get("share_with_providers") else 0,
                now_iso(),
                now_iso(),
            ),
        )
        return self.get_request(rid)

    def get_request(self, rid: str) -> dict | None:
        return self.db.one("SELECT * FROM help_request WHERE id=?", (rid,))

    def my_requests(self, seeker_id: str) -> list[dict]:
        rows = self.db.q(
            "SELECT * FROM help_request WHERE seeker_id=? ORDER BY created_at DESC", (seeker_id,)
        )
        for r in rows:
            r["service_name"] = (
                (self.store.get(r["service_id"]) or {}).get("data", {}).get("name")
                if r["service_id"]
                else None
            )
            r["org_name"] = (
                (self.store.get(r["org_id"]) or {}).get("data", {}).get("name")
                if r["org_id"]
                else None
            )
        return rows

    def _seeker_card(self, req: dict, reveal: bool) -> dict:
        u = self.db.one("SELECT name,email FROM users WHERE id=?", (req["seeker_id"],)) or {}
        prof = self.get_profile(req["seeker_id"])
        card = {
            "request_id": req["id"],
            "first_name": (u.get("name") or "A neighbor").split()[0],
            "zip": prof.get("zip"),
            "need": req["need_text"],
            "language": prof.get("language"),
            "created_at": req["created_at"],
            "status": req["status"],
            "match_score": req["match_score"],
        }
        if reveal:
            card |= {
                "name": u.get("name"),
                "email": u.get("email"),
                "phone": prof.get("phone"),
                "contact_preference": prof.get("contact_preference"),
            }
        return card

    def provider_needs(self, org_id: str, min_score: float = 3.0) -> dict:
        """Direct requests to this organization plus unassigned needs that match what it offers."""
        direct = [
            self._seeker_card(r, False)
            for r in self.db.q(
                "SELECT * FROM help_request WHERE org_id=? AND status='open' AND share_consent=1 ORDER BY created_at DESC",
                (org_id,),
            )
        ]
        suggested = []
        for r in self.db.q(
            "SELECT * FROM help_request WHERE org_id IS NULL AND status='open' AND share_consent=1 ORDER BY created_at DESC LIMIT 200"
        ):
            hits = self.search.search_services(
                r["need_text"], filters={"org_id": org_id, "status": "active"}, limit=1
            )
            if hits and hits[0]["score"] >= min_score:
                c = self._seeker_card(r, False)
                c |= {
                    "suggested_service": hits[0]["meta"]["name"],
                    "suggested_service_id": hits[0]["id"],
                    "match_score": hits[0]["score"],
                }
                suggested.append(c)
        return {"direct": direct, "suggested": suggested}

    def provider_served(self, org_id: str) -> list[dict]:
        rows = self.db.q(
            "SELECT * FROM help_request WHERE org_id=? AND status IN ('accepted','closed') ORDER BY updated_at DESC",
            (org_id,),
        )
        out = []
        for r in rows:
            card = self._seeker_card(r, True)
            card["service"] = (
                (self.store.get(r["service_id"]) or {}).get("data", {}).get("name")
                if r["service_id"]
                else None
            )
            out.append(card)
        return out

    def respond(
        self,
        org_id: str,
        request_id: str,
        action: str,
        note: str | None = None,
        service_id: str | None = None,
    ) -> dict:
        r = self.get_request(request_id)
        if not r or r["status"] not in ("open", "accepted"):
            raise KeyError(request_id)
        if r["org_id"] not in (None, org_id):
            raise PermissionError("This request belongs to another organization.")
        status = {"accept": "accepted", "decline": "declined", "close": "closed"}[action]
        sid = service_id or r["service_id"]
        self.db.run(
            "UPDATE help_request SET status=?, org_id=?, service_id=?, provider_note=?, updated_at=? WHERE id=?",
            (status, org_id, sid, note, now_iso(), request_id),
        )
        return self.get_request(request_id)
