"""OpenSearch indexer — STUB (P5 stretch, task 30.1).

Builds/rebuilds the index from the HSDS store; the store is the single source of
truth and the index is a derived read model (drop-and-recreate safe).
Spec: Requirements 36.1, 36.2, 36.3.
"""

from __future__ import annotations

from contracts.store import HsdsStore


def rebuild_index(store: HsdsStore) -> int:
    """Drop and recreate the index from current store contents; return doc count."""
    raise NotImplementedError("task 30.1: build/rebuild index from HSDS store")
