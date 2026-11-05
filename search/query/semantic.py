"""Semantic search: cosine similarity over precomputed text embeddings.

Embeddings are computed per service at ingest time (``api/app/store_pg.py``) and compared
in Python here -- no vector extension required, so this runs the same on SQLite (tests)
and Postgres, matching ``SimpleSearch``'s role for full-text search. Select it with
``RE_SEARCH_BACKEND=semantic``; the API and UI don't change (same ``SearchBackend``).
"""

from __future__ import annotations

import math

from sqlalchemy import select

from search.embeddings.embed import Embedder, get_embedder
from search.query.intent import QueryPlan
from search.query.search import SearchResults, _hit, _visible
from shared.models import Service


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


class SemanticSearch:
    name = "semantic"

    def __init__(self, store, embedder: Embedder | None = None) -> None:
        self.store = store
        self.embedder = embedder or get_embedder()

    def search(self, plan: QueryPlan, limit: int, offset: int) -> SearchResults:
        query_text = " ".join(plan.all_terms or plan.own_terms)
        if not query_text.strip():
            return SearchResults(0, 0, backend=self.name)
        query_vector = self.embedder.embed(query_text)

        with self.store.session() as session:
            visible_ids = {r for (r,) in session.execute(_visible(select(Service.id)))}
            scored, hidden = [], 0
            for row in session.scalars(select(Service)):
                if row.embedding is None or row.embedding_model_id != self.embedder.model_id:
                    continue
                score = cosine(query_vector, row.embedding)
                if score <= 0:
                    continue
                if row.id not in visible_ids:
                    hidden += 1
                    continue
                desc = (row.document.get("description") or row.search_text or "")[:220]
                scored.append(_hit(row, desc, score))
        scored.sort(key=lambda h: (-h.score, h.name))
        return SearchResults(len(scored), hidden, scored[offset : offset + limit], self.name)
