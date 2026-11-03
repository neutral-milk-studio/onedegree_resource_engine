"""Bearer token auth + per-source scoping — STUB (task 27).

Requires a valid bearer token on every endpoint; a source-scoped key sees only
its source's records and treats another source's id as not-found.
Spec: Requirements 33.1, 33.2, 33.3, 33.4.
"""

from __future__ import annotations


def resolve_source_scope(token: str) -> str | None:
    """Return the source a token is scoped to, or None for unscoped. Raise on invalid."""
    raise NotImplementedError("task 27: validate bearer token; return source scope")
