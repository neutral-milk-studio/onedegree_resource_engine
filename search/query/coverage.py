"""Need coverage by city: which common needs (``search/query/intent.py``'s ``INTENTS``)
have no visible, active service in each city.

Reuses ``INTENTS``'s own service-side vocabulary (the terms a listing's text would
actually contain, not a person's request phrasing), so "covered" means the same thing
search already means by a match -- no new taxonomy, no new data, just a different view
of what's already ingested.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from search.query.intent import INTENTS
from search.query.search import _visible
from shared.models import Service


@dataclass
class CityCoverage:
    city: str
    service_count: int
    covered: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def coverage_by_city(store) -> list[CityCoverage]:
    """One row per city with at least one visible service, sorted by name."""
    with store.session() as session:
        rows = session.execute(
            _visible(select(Service.city, Service.name, Service.search_text))
        ).all()

    texts_by_city: dict[str, list[str]] = {}
    for city, name, search_text in rows:
        if not city:
            continue
        texts_by_city.setdefault(city, []).append(f"{name}\n{search_text or ''}".lower())

    results = []
    for city, texts in sorted(texts_by_city.items()):
        covered = [
            need
            for need, (_, terms) in INTENTS.items()
            if any(term in text for text in texts for term in terms)
        ]
        missing = [need for need in INTENTS if need not in covered]
        results.append(
            CityCoverage(city=city, service_count=len(texts), covered=covered, missing=missing)
        )
    return results
