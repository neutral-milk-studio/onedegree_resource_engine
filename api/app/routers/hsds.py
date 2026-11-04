"""HSDS REST API. A bearer API key is required on every endpoint, and a key can be scoped to one
source so a caller sees only that source's records."""

from __future__ import annotations

import hashlib
import math
import secrets

from fastapi import APIRouter, Depends, Request

from api.app.hsds.nest import Views
from api.app.web import ApiError, container, current_principal
from shared.db import now_iso

router = APIRouter()
MAX_PER_PAGE = 100


def hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


def new_api_key() -> str:
    return "re_" + secrets.token_urlsafe(32)


def api_key(request: Request) -> str | None:
    """Accept an API key, or an admin's login token. Returns the key's source scope, if any."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "Send an API key as 'Authorization: Bearer <key>'.")
    token, c = header[7:], container()
    if token.startswith("re_"):
        row = c.db.one(
            "SELECT * FROM api_key WHERE key_hash=? AND revoked_at IS NULL", (hash_key(token),)
        )
        if not row:
            raise ApiError(401, "That API key is not valid.")
        c.db.run("UPDATE api_key SET last_used_at=? WHERE id=?", (now_iso(), row["id"]))
        return row["source_scope"]
    principal = current_principal(request)
    if not principal or principal.role != "admin":
        raise ApiError(401, "That token is not valid for the HSDS API.")
    return None


def paging(request: Request):
    try:
        page = max(1, int(request.query_params.get("page", 1)))
        per = min(MAX_PER_PAGE, max(1, int(request.query_params.get("per_page", 25))))
    except ValueError:
        page, per = 1, 25
    return page, per


def page_response(contents: list, total: int, page: int, per: int):
    pages = max(1, math.ceil(total / per)) if total else 1
    return {
        "total_items": total,
        "total_pages": pages,
        "page_number": page,
        "size": len(contents),
        "first_page": page == 1,
        "last_page": page >= pages,
        "empty": not contents,
        "contents": contents,
    }


def _ext(request: Request) -> bool:
    return request.query_params.get("extensions", "").lower() == "true"


@router.get("/")
def root(request: Request, scope: str | None = Depends(api_key)):
    c = container()
    return {
        "profile": "https://docs.openreferral.org/en/latest/hsds/",
        "version": c.settings.hsds_version,
        "openapi_url": "https://raw.githubusercontent.com/openreferral/specification/3.2/schema/openapi.json",
        "publisher": {"name": "One Degree Resource Engine"},
        "data_guide": "Records are HSDS JSON. Pass ?extensions=true to include x_resource_engine metadata.",
    }


@router.get("/services")
def services(request: Request, scope: str | None = Depends(api_key)):
    c, page, per = container(), *paging(request)
    a = request.query_params
    ids = None
    search = a.get("search") or a.get("q")
    if search:
        hits = c.search.search_services(
            search,
            filters={"status": "active"} if a.get("status", "active") == "active" else None,
            limit=500,
        )
        ids = [h["id"] for h in hits]
        order = {h["id"]: i for i, h in enumerate(hits)}
    rows, total = c.store.list(
        "service",
        org_id=a.get("organization_id"),
        source=scope,
        status=a.get("status"),
        modified_after=a.get("modified_after"),
        term_id=a.get("taxonomy_term_id"),
        taxonomy_id=a.get("taxonomy_id"),
        ids=ids,
        limit=per if not search else 1000,
        offset=(page - 1) * per if not search else 0,
    )
    if search:
        rows.sort(key=lambda r: order.get(r["id"], 1e9))
        total = len(rows)
        rows = rows[(page - 1) * per : page * per]
    v = Views(c.store)
    minimal = a.get("minimal", "").lower() == "true"
    if not minimal:
        v.prefetch(rows)
    return page_response(
        [v.service(r, minimal=minimal, ext=_ext(request)) for r in rows], total, page, per
    )


def _one(rtype, builder, scope):
    c = container()

    def run(rid):
        row = c.store.get(rid)
        if not row or row["type"] != rtype or (scope and row["source"] != scope):
            raise ApiError(404, "Not found.")
        v = Views(c.store)
        if rtype == "service":
            v.prefetch([row])
        return builder(v, row)

    return run


@router.get("/services/{rid}")
def service_one(rid: str, request: Request, scope: str | None = Depends(api_key)):
    return _one("service", lambda v, r: v.service(r, ext=_ext(request)), scope)(rid)


@router.get("/organizations")
def organizations(request: Request, scope: str | None = Depends(api_key)):
    c, page, per = container(), *paging(request)
    rows, total = c.store.list(
        "organization",
        source=scope,
        modified_after=request.query_params.get("modified_after"),
        limit=per,
        offset=(page - 1) * per,
    )
    v = Views(c.store)
    minimal = request.query_params.get("minimal", "").lower() == "true"
    return page_response(
        [v.organization(r, minimal=True if minimal else False, ext=_ext(request)) for r in rows],
        total,
        page,
        per,
    )


@router.get("/organizations/{rid}")
def organization_one(rid: str, request: Request, scope: str | None = Depends(api_key)):
    return _one("organization", lambda v, r: v.organization(r, ext=_ext(request)), scope)(rid)


@router.get("/service_at_locations")
def sals(request: Request, scope: str | None = Depends(api_key)):
    c, page, per = container(), *paging(request)
    rows, total = c.store.list(
        "service_at_location", source=scope, limit=per, offset=(page - 1) * per
    )
    v = Views(c.store)
    return page_response(
        [v.service_at_location(r, ext=_ext(request)) for r in rows], total, page, per
    )


@router.get("/service_at_locations/{rid}")
def sal_one(rid: str, request: Request, scope: str | None = Depends(api_key)):
    return _one(
        "service_at_location", lambda v, r: v.service_at_location(r, ext=_ext(request)), scope
    )(rid)


@router.get("/locations")
def locations(request: Request, scope: str | None = Depends(api_key)):
    c, page, per = container(), *paging(request)
    rows, total = c.store.list("location", source=scope, limit=per, offset=(page - 1) * per)
    return page_response(
        [Views(c.store).location(r, ext=_ext(request)) for r in rows], total, page, per
    )


@router.get("/locations/{rid}")
def location_one(rid: str, request: Request, scope: str | None = Depends(api_key)):
    return _one("location", lambda v, r: v.location(r, ext=_ext(request)), scope)(rid)


@router.get("/taxonomies")
def taxonomies(request: Request, scope: str | None = Depends(api_key)):
    c, page, per = container(), *paging(request)
    rows, total = c.store.list("taxonomy", limit=per, offset=(page - 1) * per)
    return page_response([r["data"] for r in rows], total, page, per)


@router.get("/taxonomy_terms")
def terms(request: Request, scope: str | None = Depends(api_key)):
    c, page, per = container(), *paging(request)
    rows, total = c.store.list(
        "taxonomy_term", limit=per, offset=(page - 1) * per, text_filter=None
    )
    v = Views(c.store)
    tax = request.query_params.get("taxonomy_id")
    out = [
        v.taxonomy_term(r, ext=_ext(request))
        for r in rows
        if not tax or r["data"].get("taxonomy_id") == tax
    ]
    return page_response(out, total, page, per)


@router.get("/taxonomy_terms/{rid}")
def term_one(rid: str, request: Request, scope: str | None = Depends(api_key)):
    return _one("taxonomy_term", lambda v, r: v.taxonomy_term(r, ext=_ext(request)), scope)(rid)
