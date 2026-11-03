"""Transformer + validator — STUB (task 13).

adapter.map() -> assign uuid5 id -> validate against pinned HSDS -> store or
quarantine. Spec: Requirements 11.1, 11.2, 11.3, 12.1, 12.4, 13.1, 13.2.
"""

from __future__ import annotations

from contracts.adapter import RawPayload, SourceAdapter
from contracts.store import HsdsStore


def transform_and_store(adapter: SourceAdapter, payload: RawPayload, store: HsdsStore) -> None:
    """Map one payload to HSDS, validate, then upsert or quarantine with a reason."""
    raise NotImplementedError("task 13: map -> uuid5 -> validate -> upsert/quarantine")
