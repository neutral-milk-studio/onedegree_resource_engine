"""Application API used by the web UI: seeker, provider and admin personas."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from api.app.routers.hsds import hash_key, new_api_key
from api.app.services.users import Principal
from api.app.web import ApiError, auth_required, container, current_principal, json_body
from shared.db import loads, new_id, now_iso

router = APIRouter()
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _pg(request: Request) -> tuple[int, int]:
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        return page, min(200, max(1, int(request.query_params.get("per_page", 25))))
    except ValueError:
        return 1, 25


def _in(ids: list) -> str:
    return ",".join("?" * len(ids)) if ids else "NULL"


def _name(row: dict | None) -> str | None:
    return (row or {}).get("data", {}).get("name")


# ---------------------------------------------------------------------------------------------- public
@router.get("/health")
def health(request: Request):
    c = container()
    return {
        "status": "ok",
        "hsds_version": c.settings.hsds_version,
        "validator": c.validator.engine(),
        "auth": c.settings.auth_mode,
        "llm": c.llm.name,
        "search": c.search.services.name,
        "database": c.db.kind,
        "blob": c.settings.blob_backend,
        "demo": c.settings.env == "local",
    }


@router.post("/auth/login")
def login(request: Request, payload: dict = Depends(json_body)):
    b = payload
    return container().auth.login(b.get("email", ""), b.get("password", ""))


@router.post("/auth/register")
def register(request: Request, payload: dict = Depends(json_body)):
    b = payload
    if not b.get("email") or not b.get("name"):
        raise ApiError(400, "Enter your name and email.")
    return container().auth.register_seeker(b["email"], b["name"], b.get("password", ""))


@router.get("/auth/me")
def me(request: Request, principal: Principal = Depends(auth_required())):
    p, c = principal, container()
    out = {"id": p.id, "email": p.email, "name": p.name, "role": p.role, "org_id": p.org_id}
    if p.org_id:
        out["org_name"] = _name(c.store.get(p.org_id))
    return out


# ---------------------------------------------------------------------------------------------- chat
@router.post("/chat")
def chat(request: Request, payload: dict = Depends(json_body)):
    b = payload
    msg = (b.get("message") or "").strip()
    if not msg:
        raise ApiError(400, "Type a message first.")
    return container().chat.handle(current_principal(request), b.get("session_id"), msg[:1000])


@router.get("/chat/history")
def chat_history(request: Request):
    p = current_principal(request)
    return {
        "messages": container().chat.history(
            request.query_params.get("session_id", ""), p.id if p else None
        )
    }


@router.post("/chat/resume")
def chat_resume(
    request: Request,
    principal: Principal = Depends(auth_required("seeker")),
    payload: dict = Depends(json_body),
):
    return container().chat.resume(principal, payload.get("session_id", ""))


@router.post("/chat/confirm")
def chat_confirm(
    request: Request,
    principal: Principal = Depends(auth_required("seeker")),
    payload: dict = Depends(json_body),
):
    b = payload
    return container().chat.confirm_request(
        principal, b.get("session_id", ""), b.get("service_id", "")
    )


# ---------------------------------------------------------------------------------------------- seeker
@router.get("/seeker/profile")
def seeker_profile(request: Request, principal: Principal = Depends(auth_required("seeker"))):
    return container().seekers.get_profile(principal.id)


@router.put("/seeker/profile")
def seeker_profile_save(
    request: Request,
    principal: Principal = Depends(auth_required("seeker")),
    payload: dict = Depends(json_body),
):
    return container().seekers.save_profile(principal.id, payload)


@router.get("/seeker/requests")
def seeker_requests(request: Request, principal: Principal = Depends(auth_required("seeker"))):
    return {"requests": container().seekers.my_requests(principal.id)}


@router.get("/search")
def search_directory(
    request: Request, principal: Principal = Depends(auth_required("seeker", "provider", "admin"))
):
    c, q = container(), request.query_params.get("q", "")
    hits = c.search.search_services(q, filters={"status": "active"}, limit=10)
    return {"results": [c.chat._card(h) for h in hits]}


# ---------------------------------------------------------------------------------------------- provider
def _own(rid: str, principal: Principal) -> dict:
    row = container().store.get(rid)
    if not row or row["type"] not in ("organization", "service", "location"):
        raise KeyError(rid)
    if row["org_id"] != principal.org_id:
        raise PermissionError("That record belongs to another organization.")
    return row


def _org_bundle(org_id: str) -> dict:
    c = container()
    org = c.store.get(org_id)
    if not org:
        raise KeyError(org_id)
    services, _ = c.store.list("service", org_id=org_id, limit=500)
    sals = [
        loads(r["data"])
        for r in c.db.q(
            "SELECT data FROM resource WHERE type='service_at_location' AND deleted=0 AND org_id=?",
            (org_id,),
        )
    ]
    locs = list(c.store.get_many(list({s["location_id"] for s in sals})).values())
    checks = {
        r["resource_id"]: r for r in c.db.q("SELECT * FROM check_result WHERE org_id=?", (org_id,))
    }
    pend = c.db.q(
        "SELECT resource_id, COUNT(*) AS n FROM change_request WHERE org_id=? AND status='pending' GROUP BY resource_id",
        (org_id,),
    )
    pmap = {r["resource_id"]: r["n"] for r in pend}
    return {
        "organization": org,
        "services": [
            {**s, "check": checks.get(s["id"]), "pending": pmap.get(s["id"], 0)} for s in services
        ],
        "locations": [{**l, "pending": pmap.get(l["id"], 0)} for l in locs],
        "org_pending": pmap.get(org_id, 0),
    }


@router.get("/provider/organization")
def provider_org(request: Request, principal: Principal = Depends(auth_required("provider"))):
    if not principal.org_id:
        raise ApiError(
            409, "Your account is not linked to an organization yet. Ask a One Degree admin."
        )
    return _org_bundle(principal.org_id)


@router.get("/provider/records/{rid}")
def provider_record(
    rid: str, request: Request, principal: Principal = Depends(auth_required("provider"))
):
    c, row = container(), _own(rid, principal)
    return {
        "record": row,
        "history": c.store.history(rid, 50),
        "change_requests": c.proposals.list(resource_id=rid, limit=20),
    }


@router.post("/provider/change-requests", status_code=201)
def provider_cr(
    request: Request,
    principal: Principal = Depends(auth_required("provider")),
    payload: dict = Depends(json_body),
):
    c, b = container(), payload
    row = _own(b.get("resource_id", ""), principal)
    patch = b.get("patch") or {}
    if not patch:
        raise ApiError(400, "Change at least one field.")
    errors = c.store.validate_patch(row["id"], patch)
    if errors:
        raise ApiError(422, "That change would produce invalid HSDS data.", details=errors)
    cur = row["data"]
    cid = c.proposals.create(
        resource_id=row["id"],
        resource_type=row["type"],
        org_id=row["org_id"],
        kind="provider_update",
        proposed_by_type="provider",
        proposed_by=principal.email,
        patch={f: {"current": cur.get(f), "proposed": v} for f, v in patch.items()},
        title=f"Update to {row['data'].get('name', row['type'])} from {principal.name}",
    )
    return {"id": cid, "status": "pending"}


@router.get("/provider/change-requests")
def provider_crs(request: Request, principal: Principal = Depends(auth_required("provider"))):
    return {"change_requests": container().proposals.list(org_id=principal.org_id, limit=100)}


@router.get("/provider/needs")
def provider_needs(request: Request, principal: Principal = Depends(auth_required("provider"))):
    return container().seekers.provider_needs(principal.org_id)


@router.get("/provider/served")
def provider_served(request: Request, principal: Principal = Depends(auth_required("provider"))):
    return {"served": container().seekers.provider_served(principal.org_id)}


@router.post("/provider/requests/{rid}/respond")
def provider_respond(
    rid: str,
    request: Request,
    principal: Principal = Depends(auth_required("provider")),
    payload: dict = Depends(json_body),
):
    b = payload
    if b.get("action") not in ("accept", "decline", "close"):
        raise ApiError(400, "Choose accept, decline or close.")
    return container().seekers.respond(
        principal.org_id, rid, b["action"], b.get("note"), b.get("service_id")
    )


async def _upload(request: Request, principal: Principal, auto: bool, org_id: str | None):
    form = await request.form()
    upload = form.get("file")
    if not upload or not getattr(upload, "filename", None):
        raise ApiError(400, "Choose a file to upload.")
    data = await upload.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "That file is larger than 25 MB.")
    doc = await run_in_threadpool(
        container().documents.upload,
        filename=upload.filename,
        data=data,
        title=form.get("title") or upload.filename,
        uploaded_by=principal.email,
        org_id=org_id,
        service_id=form.get("service_id") or None,
        visibility=form.get("visibility", "public"),
        effective_date=form.get("effective_date") or None,
        auto_approve=auto,
        content_type=upload.content_type,
    )
    return JSONResponse(doc, status_code=201)


@router.post("/provider/documents")
async def provider_upload(
    request: Request, principal: Principal = Depends(auth_required("provider"))
):
    return await _upload(request, principal, False, principal.org_id)


@router.get("/provider/documents")
def provider_docs(request: Request, principal: Principal = Depends(auth_required("provider"))):
    return {"documents": container().documents.list(org_id=principal.org_id)}


# ---------------------------------------------------------------------------------------------- admin
def _enrich_crs(crs: list[dict]) -> list[dict]:
    c = container()
    rows = c.store.get_many(list({x["resource_id"] for x in crs}))
    for x in crs:
        r = rows.get(x["resource_id"])
        x["resource_name"] = _name(r) or x["resource_type"]
        x["org_name"] = _name(c.store.get(x["org_id"])) if x.get("org_id") else None
        x["stale_fields"] = c.proposals.stale_fields(x) if x["status"] == "pending" else []
    return crs


@router.get("/admin/stats")
def admin_stats(request: Request, principal: Principal = Depends(auth_required("admin"))):
    c = container()
    cats = {
        r["category"]: r["n"]
        for r in c.db.q("SELECT category, COUNT(*) AS n FROM check_result GROUP BY category")
    }
    total_services = c.store.counts().get("service", 0)
    cats["unchecked"] = max(0, total_services - sum(cats.values()))
    pend = {
        r["kind"]: r["n"]
        for r in c.db.q(
            "SELECT kind, COUNT(*) AS n FROM change_request WHERE status='pending' GROUP BY kind"
        )
    }
    return {
        "counts": c.store.counts(),
        "check_categories": cats,
        "pending_by_kind": pend,
        "seekers": c.db.one("SELECT COUNT(*) AS n FROM users WHERE role='seeker'")["n"],
        "providers": c.db.one("SELECT COUNT(*) AS n FROM users WHERE role='provider'")["n"],
        "open_requests": c.db.one("SELECT COUNT(*) AS n FROM help_request WHERE status='open'")[
            "n"
        ],
        "docs_pending": c.db.one("SELECT COUNT(*) AS n FROM document WHERE status='pending'")["n"],
        "quarantined": c.db.one("SELECT COUNT(*) AS n FROM quarantine")["n"],
        "last_imports": c.db.q("SELECT * FROM import_run ORDER BY started_at DESC LIMIT 5"),
        "last_maintenance": c.db.q(
            "SELECT * FROM maintenance_run ORDER BY started_at DESC LIMIT 3"
        ),
    }


@router.get("/admin/seekers")
def admin_seekers(request: Request, principal: Principal = Depends(auth_required("admin"))):
    c = container()
    out = []
    for u in c.users.list("seeker"):
        prof = c.seekers.get_profile(u["id"])
        n = c.db.one("SELECT COUNT(*) AS n FROM help_request WHERE seeker_id=?", (u["id"],))["n"]
        out.append(
            {
                **u,
                "zip": prof.get("zip"),
                "shares": bool(prof.get("share_with_providers")),
                "requests": n,
            }
        )
    return {"seekers": out}


@router.get("/admin/seekers/{uid}")
def admin_seeker(
    uid: str, request: Request, principal: Principal = Depends(auth_required("admin"))
):
    c = container()
    u = c.users.get(uid)
    if not u or u["role"] != "seeker":
        raise KeyError(uid)
    return {
        "user": u,
        "profile": c.seekers.get_profile(uid),
        "requests": c.seekers.my_requests(uid),
    }


@router.get("/admin/providers")
def admin_providers(request: Request, principal: Principal = Depends(auth_required("admin"))):
    c, (page, per) = container(), _pg(request)
    rows, total = c.store.list(
        "organization",
        limit=per,
        offset=(page - 1) * per,
        text_filter=request.query_params.get("q") or None,
    )
    ids = [r["id"] for r in rows]
    svc = {
        r["org_id"]: r["n"]
        for r in c.db.q(
            f"SELECT org_id, COUNT(*) AS n FROM resource WHERE type='service' AND deleted=0 AND org_id IN ({_in(ids)}) GROUP BY org_id",
            ids,
        )
    }
    chk: dict = {}
    for r in c.db.q(
        f"SELECT org_id, category, COUNT(*) AS n FROM check_result WHERE org_id IN ({_in(ids)}) GROUP BY org_id, category",
        ids,
    ):
        chk.setdefault(r["org_id"], {})[r["category"]] = r["n"]
    pend = {
        r["org_id"]: r["n"]
        for r in c.db.q(
            f"SELECT org_id, COUNT(*) AS n FROM change_request WHERE status='pending' AND org_id IN ({_in(ids)}) GROUP BY org_id",
            ids,
        )
    }
    return {
        "total": total,
        "page": page,
        "organizations": [
            {
                "id": r["id"],
                "name": r["data"].get("name"),
                "website": r["data"].get("website"),
                "source": r["source"],
                "services": svc.get(r["id"], 0),
                "checks": chk.get(r["id"], {}),
                "pending": pend.get(r["id"], 0),
                "flags": r["data"].get("x_resource_engine", {}).get("flags", []),
            }
            for r in rows
        ],
    }


@router.get("/admin/providers/{org_id}")
def admin_provider(
    org_id: str, request: Request, principal: Principal = Depends(auth_required("admin"))
):
    c = container()
    out = _org_bundle(org_id)
    out["users"] = [u for u in c.users.list("provider") if u["org_id"] == org_id]
    out["change_requests"] = _enrich_crs(c.proposals.list(org_id=org_id, limit=50))
    out["documents"] = c.documents.list(org_id=org_id)
    return out


@router.get("/admin/records/{rid}")
def admin_record(
    rid: str, request: Request, principal: Principal = Depends(auth_required("admin"))
):
    c, row = container(), None
    row = c.store.get(rid)
    if not row:
        raise KeyError(rid)
    return {
        "record": row,
        "history": c.store.history(rid, 100),
        "check": c.db.one("SELECT * FROM check_result WHERE resource_id=?", (rid,)),
        "change_requests": _enrich_crs(c.proposals.list(resource_id=rid, limit=30)),
    }


@router.put("/admin/records/{rid}")
def admin_edit(
    rid: str,
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    c, b = container(), payload
    res = c.store.apply_patch(
        rid,
        b.get("patch") or {},
        "admin",
        principal.email,
        note=b.get("note"),
        expected_version=b.get("expected_version"),
    )
    return res


@router.get("/admin/change-requests")
def admin_crs(request: Request, principal: Principal = Depends(auth_required("admin"))):
    a = request.query_params
    crs = container().proposals.list(
        status=a.get("status", "pending"),
        kind=a.get("kind") or None,
        org_id=a.get("org_id") or None,
        limit=300,
    )
    return {"change_requests": _enrich_crs(crs)}


@router.post("/admin/change-requests/{cid}/approve")
def admin_approve(
    cid: str,
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    b = payload
    return container().proposals.approve(
        cid, principal.email, b.get("note"), b.get("overrides"), bool(b.get("force"))
    )


@router.post("/admin/change-requests/{cid}/reject")
def admin_reject(
    cid: str,
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    container().proposals.reject(cid, principal.email, payload.get("note"))
    return {"status": "rejected"}


@router.post("/admin/maintenance/run")
def admin_maint_run(
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    b = payload
    scope = "service" if b.get("service_id") else "all"
    return container().jobs.start(
        "maintenance",
        wait=bool(b.get("wait")) or request.query_params.get("wait") == "true",
        scope=scope,
        service_id=b.get("service_id"),
        force=bool(b.get("force")),
        limit=b.get("limit"),
        who=principal.email,
    )


@router.get("/admin/maintenance/results")
def admin_maint_results(request: Request, principal: Principal = Depends(auth_required("admin"))):
    c, a, (page, per) = container(), request.query_params, _pg(request)
    where, p = ["r.type='service'", "r.deleted=0"], []
    cat = a.get("category")
    if cat == "unchecked":
        where.append("c.resource_id IS NULL")
    elif cat:
        where.append("c.category=?")
        p.append(cat)
    if a.get("q"):
        where.append("LOWER(r.data) LIKE ?")
        p.append(f"%{a['q'].lower()}%")
    if a.get("source"):
        where.append("r.source=?")
        p.append(a["source"])
    w = " AND ".join(where)
    total = c.db.one(
        f"SELECT COUNT(*) AS n FROM resource r LEFT JOIN check_result c ON c.resource_id=r.id WHERE {w}",
        p,
    )["n"]
    rows = c.db.q(
        f"SELECT r.id, r.org_id, r.source, r.data, r.version, c.category, c.summary, c.url, c.checked_at, c.verified_marker "
        f"FROM resource r LEFT JOIN check_result c ON c.resource_id=r.id WHERE {w} "
        f"ORDER BY CASE c.category WHEN 'actively_bad' THEN 0 WHEN 'check_failed' THEN 1 WHEN 'discrepancy' THEN 2 "
        f"WHEN 'missing_information' THEN 3 WHEN 'verified' THEN 5 ELSE 4 END, r.id LIMIT ? OFFSET ?",
        p + [per, (page - 1) * per],
    )
    orgs = c.store.get_many(list({r["org_id"] for r in rows if r["org_id"]}))
    pend = {
        r["resource_id"]: r["n"]
        for r in c.db.q(
            f"SELECT resource_id, COUNT(*) AS n FROM change_request WHERE status='pending' AND resource_id IN ({_in([r['id'] for r in rows])}) GROUP BY resource_id",
            [r["id"] for r in rows],
        )
    }
    return {
        "total": total,
        "page": page,
        "results": [
            {
                "id": r["id"],
                "name": loads(r["data"])["name"],
                "organization": _name(orgs.get(r["org_id"])),
                "org_id": r["org_id"],
                "source": r["source"],
                "category": r["category"] or "unchecked",
                "summary": r["summary"],
                "url": r["url"],
                "checked_at": r["checked_at"],
                "verified": bool(r["verified_marker"]),
                "pending": pend.get(r["id"], 0),
            }
            for r in rows
        ],
    }


@router.get("/admin/maintenance/runs")
def admin_maint_runs(request: Request, principal: Principal = Depends(auth_required("admin"))):
    rows = container().db.q("SELECT * FROM maintenance_run ORDER BY started_at DESC LIMIT 20")
    for r in rows:
        r["counts"] = loads(r["counts"], {})
    return {"runs": rows}


@router.post("/admin/imports/run")
def admin_import_run(
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    b = payload
    return container().jobs.start(
        "import",
        wait=bool(b.get("wait")) or request.query_params.get("wait") == "true",
        source=b.get("source"),
        who=principal.email,
    )


@router.get("/admin/imports")
def admin_imports(request: Request, principal: Principal = Depends(auth_required("admin"))):
    c = container()
    runs = c.db.q("SELECT * FROM import_run ORDER BY started_at DESC LIMIT 30")
    for r in runs:
        r["balanced"] = (
            r["received"] == r["stored"] + r["quarantined"] if r["status"] != "running" else None
        )
    return {
        "runs": runs,
        "sources": [
            {"name": s.name, "adapter": s.adapter, "location": s.location, "enabled": s.enabled}
            for s in c.sources()
        ],
    }


@router.get("/admin/quarantine")
def admin_quarantine(request: Request, principal: Principal = Depends(auth_required("admin"))):
    rows = container().db.q(
        "SELECT id, run_id, source, native_id, reason, created_at FROM quarantine ORDER BY created_at DESC LIMIT 200"
    )
    return {"quarantine": rows}


@router.get("/admin/api-keys")
def admin_keys(request: Request, principal: Principal = Depends(auth_required("admin"))):
    return {
        "keys": container().db.q(
            "SELECT id,name,key_prefix,source_scope,created_by,created_at,revoked_at,last_used_at FROM api_key ORDER BY created_at DESC"
        )
    }


@router.post("/admin/api-keys", status_code=201)
def admin_key_create(
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    b, c = payload, container()
    if not b.get("name"):
        raise ApiError(400, "Name the key so you can recognize it later.")
    key = new_api_key()
    kid = new_id()
    c.db.run(
        "INSERT INTO api_key (id,name,key_hash,key_prefix,source_scope,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
        (
            kid,
            b["name"],
            hash_key(key),
            key[:8],
            b.get("source_scope") or None,
            principal.email,
            now_iso(),
        ),
    )
    return {"id": kid, "key": key, "note": "Copy this key now. It is not shown again."}


@router.delete("/admin/api-keys/{kid}")
def admin_key_revoke(
    kid: str, request: Request, principal: Principal = Depends(auth_required("admin"))
):
    container().db.run("UPDATE api_key SET revoked_at=? WHERE id=?", (now_iso(), kid))
    return {"revoked": True}


@router.get("/admin/users")
def admin_users(request: Request, principal: Principal = Depends(auth_required("admin"))):
    return {"users": container().users.list()}


@router.post("/admin/users", status_code=201)
def admin_user_create(
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    c, b = container(), payload
    if b.get("role") not in ("provider", "admin") or not b.get("email") or not b.get("name"):
        raise ApiError(400, "Enter a name, an email and a role (provider or admin).")
    if b["role"] == "provider" and not c.store.get(b.get("org_id") or ""):
        raise ApiError(400, "Choose the organization this provider account belongs to.")
    if c.settings.auth_mode == "cognito":  # pragma: no cover
        c.auth.create_provider_login(b["email"], b["name"])
    u = c.users.create(b["email"], b["name"], b["role"], b.get("password"), b.get("org_id"))
    return u


@router.get("/admin/documents")
def admin_docs(request: Request, principal: Principal = Depends(auth_required("admin"))):
    docs = container().documents.list(status=request.query_params.get("status") or None)
    return {"documents": docs}


@router.post("/admin/documents")
async def admin_upload(request: Request, principal: Principal = Depends(auth_required("admin"))):
    form = await request.form()
    return await _upload(request, principal, True, form.get("org_id") or None)


@router.post("/admin/documents/{did}/decide")
def admin_doc_decide(
    did: str,
    request: Request,
    principal: Principal = Depends(auth_required("admin")),
    payload: dict = Depends(json_body),
):
    try:
        container().documents.decide(did, bool(payload.get("approve")), principal.email)
    except ValueError as e:
        raise ApiError(422, str(e))
    return {"ok": True}


@router.post("/admin/reindex")
def admin_reindex(request: Request, principal: Principal = Depends(auth_required("admin"))):
    return container().search.reindex()
