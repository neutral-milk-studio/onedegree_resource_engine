"""Semantic search: the offline embedder, cosine ranking, and the backend end to end
against the real sample data (``loaded_store``) -- the Bedrock call itself is stubbed
(same convention as ``maintenance.tests.test_extract``'s ``StubBedrock``: it costs real
money and needs real AWS credentials, so it can't be exercised for real here)."""

from __future__ import annotations

import json

import pytest

from search.embeddings.embed import BedrockEmbedder, EmbeddingError, OfflineEmbedder, get_embedder
from search.indexer.build_index import rebuild_embeddings
from search.query.intent import plan
from search.query.semantic import SemanticSearch, cosine


class StubBedrockRuntime:
    def __init__(self, embedding: list[float] | Exception) -> None:
        self.embedding, self.calls = embedding, []

    def invoke_model(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.embedding, Exception):
            raise self.embedding
        body = json.dumps({"embedding": self.embedding}).encode()

        class Body:
            def read(self) -> bytes:
                return body

        return {"body": Body()}


def test_offline_embedder_is_deterministic_and_unit_length():
    embedder = OfflineEmbedder(dimensions=32)
    a = embedder.embed("rent assistance for families")
    b = embedder.embed("rent assistance for families")
    assert a == b
    assert abs(sum(v * v for v in a) ** 0.5 - 1.0) < 1e-9


def test_offline_embedder_ranks_shared_vocabulary_closer():
    embedder = OfflineEmbedder(dimensions=64)
    rent = embedder.embed("help paying rent and avoiding eviction")
    rent_again = embedder.embed("rent help, eviction prevention assistance")
    unrelated = embedder.embed("youth after school tutoring program")
    assert cosine(rent, rent_again) > cosine(rent, unrelated)


def test_cosine_handles_mismatched_or_empty_vectors():
    assert cosine([], [1.0]) == 0.0
    assert cosine([1.0, 0.0], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


def test_bedrock_embedder_parses_titan_reply():
    client = StubBedrockRuntime([0.1, 0.2, 0.3])
    embedder = BedrockEmbedder(client, "amazon.titan-embed-text-v2:0")
    assert embedder.embed("food pantry") == [0.1, 0.2, 0.3]
    assert json.loads(client.calls[0]["body"]) == {"inputText": "food pantry"}


def test_bedrock_embedder_failure_is_an_embedding_error():
    embedder = BedrockEmbedder(StubBedrockRuntime(RuntimeError("throttled")), "m")
    with pytest.raises(EmbeddingError):
        embedder.embed("x")


def test_get_embedder_uses_bedrock_only_when_configured(monkeypatch):
    monkeypatch.delenv("BEDROCK_EMBEDDING_MODEL_ID", raising=False)
    assert isinstance(get_embedder(), OfflineEmbedder)
    monkeypatch.setenv("BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
    assert isinstance(get_embedder(), BedrockEmbedder)


def test_every_ingested_service_already_has_an_offline_embedding(loaded_store):
    """Ingest embeds on save (api/app/store_pg.py); no separate backfill step needed."""
    from sqlalchemy import select

    from shared.models import Service

    with loaded_store.session() as session:
        rows = session.scalars(select(Service)).all()
    assert rows and all(r.embedding and r.embedding_model_id == "offline-hashing" for r in rows)
    assert rebuild_embeddings(loaded_store) == 0  # every save already keeps embeddings current


def test_semantic_search_finds_rent_help_by_meaning_not_just_keywords(loaded_store):
    backend = SemanticSearch(loaded_store)
    results = backend.search(plan("I just lost my job and can't pay rent"), limit=10, offset=0)
    assert results.backend == "semantic"
    assert results.hits
    assert all(h.score > 0 for h in results.hits)
    scores = [h.score for h in results.hits]
    assert scores == sorted(scores, reverse=True)


def test_semantic_search_hides_flagged_and_inactive_listings(loaded_store):
    from shared.models import Service

    backend = SemanticSearch(loaded_store)
    before = backend.search(plan("emergency rent relief grants"), limit=50, offset=0)
    with loaded_store.session() as session:
        any_hit = session.get(Service, before.hits[0].service_id)
        any_hit.status = "inactive"
    after = backend.search(plan("emergency rent relief grants"), limit=50, offset=0)
    assert after.total == before.total - 1
    assert after.hidden >= before.hidden


def test_empty_query_returns_no_results(loaded_store):
    backend = SemanticSearch(loaded_store)
    assert backend.search(plan("   "), limit=10, offset=0).hits == []
