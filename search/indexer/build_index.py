"""Search text for a service, and (re)building it for every stored service.

The HSDS store is the single source of truth; search text is derived from it, so it can be
rebuilt at any time. Today it is a column searched with Postgres full-text search; the same
text is what an OpenSearch index would ingest.
"""

from __future__ import annotations

from sqlalchemy import select


def search_text(doc: dict) -> str:
    """Everything a person might search by, from one nested HSDS service."""
    org = doc.get("organization") or {}
    parts: list[str] = [doc.get("name", ""), org.get("name", ""), doc.get("alternate_name", "")]
    parts += [doc.get("description", ""), doc.get("eligibility_description", "")]
    parts += [a.get("name", "") for a in doc.get("service_areas") or []]
    parts += [lang.get("name", "") for lang in doc.get("languages") or []]
    for attr in doc.get("attributes") or []:
        term = attr.get("taxonomy_term") or {}
        parts += [term.get("name", ""), term.get("description", "")]
    for sal in doc.get("service_at_locations") or []:
        for addr in (sal.get("location") or {}).get("addresses") or []:
            parts.append(addr.get("city", ""))
    seen, out = set(), []
    for part in parts:
        part = (part or "").strip()
        if part and part not in seen:
            seen.add(part)
            out.append(part)
    return "\n".join(out)


def rebuild_index(store) -> int:
    """Recompute search text for every stored service; return how many were updated."""
    from shared.models import Service

    count = 0
    with store.session() as session:
        for row in session.scalars(select(Service)):
            text = search_text(row.document)
            if row.search_text != text:
                row.search_text = text
                count += 1
    return count


def rebuild_embeddings(store, embedder=None) -> int:
    """Recompute embeddings for services with none, or embedded by a since-changed model.

    Standalone counterpart to :func:`rebuild_index`, for backfilling a store after
    ``search/embeddings`` is turned on or its model changes -- normal ingest already embeds
    every record it creates or changes (``api/app/store_pg.py``).
    """
    from search.embeddings.embed import get_embedder
    from shared.models import Service

    embedder = embedder or get_embedder()
    count = 0
    with store.session() as session:
        for row in session.scalars(select(Service)):
            if row.embedding is not None and row.embedding_model_id == embedder.model_id:
                continue
            row.embedding = embedder.embed(search_text(row.document) or row.name)
            row.embedding_model_id = embedder.model_id
            count += 1
    return count
