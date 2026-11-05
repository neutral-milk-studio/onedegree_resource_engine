"""Text embeddings for semantic search.

Amazon Bedrock Titan Text Embeddings when ``BEDROCK_EMBEDDING_MODEL_ID`` is set, otherwise
a deterministic offline embedder -- the same "works without AWS" split as
``maintenance.extractor.extract``'s offline-rules vs. Bedrock extractor.

Bedrock has no embeddings call in the Converse API, so this uses ``invoke_model`` directly
(Titan's ``{"inputText": ...}`` -> ``{"embedding": [...]}`` contract) rather than the
Converse client the extractor uses.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from typing import Any, Protocol

log = logging.getLogger(__name__)

OFFLINE_MODEL_ID = "offline-hashing"
DEFAULT_DIMENSIONS = 256


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model_id: str

    def embed(self, text: str) -> list[float]: ...


class OfflineEmbedder:
    """Deterministic hashed bag-of-words embedding: no network, no API key, same every run.

    Each word hashes into one of ``dimensions`` buckets with a hashed sign, and the vector
    is L2-normalized. It's not a real semantic embedding (it can't tell "doctor" is close to
    "physician"), but shared vocabulary still scores higher under cosine similarity than
    unrelated text, which is enough to exercise semantic search end to end -- same interface,
    same ranking code -- before a Bedrock budget exists.
    """

    model_id = OFFLINE_MODEL_ID

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            vector[bucket] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector


class BedrockEmbedder:
    """Amazon Bedrock Titan Text Embeddings. Construct with a boto3 ``bedrock-runtime`` client."""

    def __init__(self, client: Any, model_id: str, max_chars: int = 8000) -> None:
        self.client = client
        self.model_id = model_id
        self.max_chars = max_chars

    def embed(self, text: str) -> list[float]:
        try:
            response = self.client.invoke_model(
                modelId=self.model_id,
                body=json.dumps({"inputText": text[: self.max_chars]}),
                accept="application/json",
                contentType="application/json",
            )
            payload = json.loads(response["body"].read())
        except Exception as exc:  # noqa: BLE001 - any Bedrock failure is a failed embed
            raise EmbeddingError(f"Bedrock embedding call failed: {exc}") from exc
        embedding = payload.get("embedding")
        if not embedding:
            raise EmbeddingError(f"Bedrock reply had no embedding: {payload}")
        log.info("bedrock embedding", extra={"model_id": self.model_id, "chars": len(text)})
        return embedding


def get_embedder() -> Embedder:
    """Bedrock when ``BEDROCK_EMBEDDING_MODEL_ID`` is set, otherwise the offline embedder."""
    model_id = os.environ.get("BEDROCK_EMBEDDING_MODEL_ID", "").strip()
    if not model_id:
        return OfflineEmbedder()
    import boto3

    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2"))
    return BedrockEmbedder(client, model_id)
