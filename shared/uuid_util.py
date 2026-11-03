"""Deterministic HSDS id generation.

Record ids are UUIDv5 derived from the source name plus the source's native id,
so re-ingesting a source updates existing records instead of duplicating them.

Spec: Requirement 12.1.
"""

from __future__ import annotations

import uuid

# Fixed namespace for the Resource Engine. Deriving ids under one stable
# namespace keeps them reproducible across runs and machines. Do not change this
# value once records exist, or ids will shift.
NAMESPACE = uuid.UUID("6f3b9c2a-1e5d-5a7b-9c4e-0a1b2c3d4e5f")


def hsds_id(source: str, native_id: str) -> str:
    """Return the deterministic HSDS id for a (source, native_id) pair.

    Same inputs always yield the same id; distinct inputs yield distinct ids.
    """
    return str(uuid.uuid5(NAMESPACE, f"{source}:{native_id}"))
