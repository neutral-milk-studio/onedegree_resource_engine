"""Search over the HSDS store behind one interface, so OpenSearch can replace Postgres.

``SearchBackend.search(plan, limit, offset)`` returns ranked active services. Listings with
an open ``suspicious`` or ``actively_bad`` finding, and inactive ones, are never returned;
how many were hidden is reported so staff can see the filter working.

Backends:
- ``PostgresSearch`` (default): full-text search with a GIN index, prefix matching, the
  person's own words ranked above added ones, and highlighted snippets.
- ``SimpleSearch``: in-Python term matching for SQLite (unit tests and quick local runs).
- ``SemanticSearch`` (``search/query/semantic.py``): cosine similarity over precomputed
  Bedrock (or offline) embeddings. Select any backend with ``RE_SEARCH_BACKEND``; the API
  and UI don't change.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import exists, func, literal_column, select

from search.query.intent import QueryPlan
from shared.models import Finding, Service

HIDDEN_CATEGORIES = ("suspicious", "actively_bad")


@dataclass
class SearchHit:
    service_id: str
    name: str
    organization_name: str | None
    snippet: str  # «highlighted» terms marked with guillemets
    city: str | None
    phone: str | None
    website: str | None
    source: str
    score: float
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class SearchResults:
    total: int
    hidden: int
    hits: list[SearchHit] = field(default_factory=list)
    backend: str = ""


class SearchBackend(Protocol):
    name: str

    def search(self, plan: QueryPlan, limit: int, offset: int) -> SearchResults: ...


def _visible(query):
    flagged = exists().where(
        Finding.service_id == Service.id,
        Finding.review_state == "pending",
        Finding.category.in_(HIDDEN_CATEGORIES),
    )
    return query.where(Service.status == "active", ~flagged)


def _tsquery(terms: list[str]) -> str:
    """['rent', 'financial assistance'] -> "rent:* | (financial:* & assistance:*)"."""
    parts = []
    for term in terms:
        words = [w for w in re.findall(r"[a-z0-9]+", term.lower()) if w]
        if not words:
            continue
        group = " & ".join(f"{w}:*" for w in words)
        parts.append(f"({group})" if len(words) > 1 else group)
    return " | ".join(parts)


class PostgresSearch:
    name = "postgres-fts"

    def __init__(self, store) -> None:
        self.store = store

    def search(self, plan: QueryPlan, limit: int, offset: int) -> SearchResults:
        all_terms = _tsquery(plan.all_terms)
        own_terms = _tsquery(plan.own_terms) or all_terms
        if not all_terms:
            return SearchResults(0, 0, backend=self.name)
        # Query text only ever reaches SQL as bound parameters.
        cfg = literal_column("'english'::regconfig")
        q_all = func.to_tsquery(cfg, all_terms)
        q_own = func.to_tsquery(cfg, own_terms)
        vector = func.to_tsvector(cfg, func.coalesce(Service.search_text, ""))
        weighted = func.setweight(func.to_tsvector(cfg, Service.name), literal_column("'A'")).op(
            "||"
        )(func.setweight(vector, literal_column("'B'")))
        score = (2 * func.ts_rank_cd(weighted, q_own) + func.ts_rank_cd(vector, q_all)).label(
            "score"
        )
        description = Service.document["description"].as_string()
        snippet = func.ts_headline(
            cfg,
            func.coalesce(description, Service.search_text, ""),
            q_all,
            literal_column("'StartSel=«, StopSel=», MaxWords=28, MinWords=10, MaxFragments=1'"),
        ).label("snippet")
        matches = vector.op("@@")(q_all)
        with self.store.session() as session:
            matching = session.scalar(select(func.count()).select_from(Service).where(matches))
            visible = _visible(select(Service.id).where(matches))
            total = session.scalar(select(func.count()).select_from(visible.subquery()))
            rows = session.execute(
                _visible(select(Service, score, snippet).where(matches))
                .order_by(score.desc(), Service.name)
                .offset(offset)
                .limit(limit)
            ).all()
            hits = [_hit(row.Service, row.snippet, float(row.score)) for row in rows]
        return SearchResults(total or 0, (matching or 0) - (total or 0), hits, self.name)


def _stem(word: str) -> str:
    for suffix in ("ies", "ing", "es", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


class SimpleSearch:
    """Term matching in Python, for SQLite. Same filters and result shape as Postgres."""

    name = "simple"

    def __init__(self, store) -> None:
        self.store = store

    def search(self, plan: QueryPlan, limit: int, offset: int) -> SearchResults:
        own = [_stem(w) for t in plan.own_terms for w in t.split()]
        added = [_stem(w) for t in plan.added_terms for w in t.split()]
        with self.store.session() as session:
            visible_ids = {r for (r,) in session.execute(_visible(select(Service.id)))}
            scored = []
            hidden = 0
            for row in session.scalars(select(Service)):
                words = {
                    _stem(w) for w in re.findall(r"[a-z0-9]+", (row.search_text or "").lower())
                }
                name_words = {_stem(w) for w in re.findall(r"[a-z0-9]+", row.name.lower())}
                score = sum(2 + (w in name_words) for w in own if w in words)
                score += sum(1 for w in added if w in words)
                if not score:
                    continue
                if row.id not in visible_ids:
                    hidden += 1
                    continue
                desc = (row.document.get("description") or row.search_text or "")[:220]
                scored.append(_hit(row, desc, float(score)))
        scored.sort(key=lambda h: (-h.score, h.name))
        return SearchResults(len(scored), hidden, scored[offset : offset + limit], self.name)


def _coordinates(doc: dict) -> tuple[float | None, float | None]:
    """Lat/lon of the service's first located address, if it has one and the address
    carries coordinates -- not every HSDS location does."""
    for sal in doc.get("service_at_locations") or []:
        location = sal.get("location") or {}
        lat, lon = location.get("latitude"), location.get("longitude")
        if lat is not None and lon is not None:
            return float(lat), float(lon)
    return None, None


def _hit(row: Service, snippet: str, score: float) -> SearchHit:
    latitude, longitude = _coordinates(row.document)
    return SearchHit(
        service_id=row.id,
        name=row.name,
        organization_name=row.organization_name,
        snippet=" ".join((snippet or "").split()),
        city=row.city,
        phone=row.phone,
        website=row.website,
        source=row.source,
        score=round(score, 4),
        latitude=latitude,
        longitude=longitude,
    )


def get_backend(store) -> SearchBackend:
    """``RE_SEARCH_BACKEND`` ("postgres" | "simple" | "semantic") picks explicitly;
    otherwise full-text search, backend chosen by dialect (Postgres FTS vs. in-Python)."""
    choice = os.environ.get("RE_SEARCH_BACKEND", "").strip().lower()
    if choice == "semantic":
        from search.query.semantic import SemanticSearch

        return SemanticSearch(store)
    if choice == "postgres":
        return PostgresSearch(store)
    if choice == "simple":
        return SimpleSearch(store)
    dialect = store.engine.dialect.name
    return PostgresSearch(store) if dialect == "postgresql" else SimpleSearch(store)


def search_services(query: str) -> list[dict]:
    """Compatibility with the original stub: HSDS service ids matching ``query``."""
    raise NotImplementedError("use get_backend(store).search(plan(query), limit, offset)")
