"""S3 raw store writer — STUB (task 12).

Writes each fetched payload byte-for-byte to S3 BEFORE any transformation,
keyed source/<fetched_at ISO8601>/<native_id|batch_seq>.json. Encrypted at rest.
Spec: Requirements 10.1, 10.2, 10.3, 45.3.
"""

from __future__ import annotations

from contracts.adapter import RawPayload


def write_raw(source: str, native_id: str, payload: RawPayload) -> str:
    """Write the payload as received; return the raw-store reference (S3 key)."""
    raise NotImplementedError("task 12: write payload to S3 as received, return key")
