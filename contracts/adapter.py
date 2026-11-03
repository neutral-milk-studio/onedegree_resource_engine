"""Adapter contract — the one interface every source implements.

Adding a source is configuration plus one module that satisfies this contract;
the core ingest pipeline does not change.

Spec: Requirements 1.1, 8.1, 8.2, 13.1 (section 5.4 adapter contract).
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

# A raw payload is opaque to the pipeline. It is written to the raw store exactly
# as received before anything else happens.
RawPayload = dict[str, Any]

# HSDS objects produced for ONE source record, grouped by object type, e.g.
# {"organization": [...], "service": [...], "location": [...], ...}
HsdsObjects = dict[str, list[dict[str, Any]]]


class MapError(Exception):
    """Raised by an adapter for a single record it cannot map.

    The pipeline turns a MapError into a quarantine entry for that record and
    continues the batch. An adapter MUST NOT raise for the whole batch when a
    single record is unmappable (section 5.4.5).
    """

    def __init__(self, native_id: str, reason: str) -> None:
        self.native_id = native_id
        self.reason = reason
        super().__init__(f"cannot map record {native_id!r}: {reason}")


@runtime_checkable
class SourceAdapter(Protocol):
    """One interface for every source.

    Implementations live under ``ingest/adapters/`` and are selected by
    configuration. ``name`` is the stable source identifier used both in the
    deterministic HSDS id and in ``x_resource_engine.source.system``.
    """

    name: str

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        """Yield raw payloads from the source.

        Each yielded payload is written to the raw store untouched BEFORE any
        transformation. ``since`` is an optional watermark for incremental
        fetches; ``None`` fetches everything.
        """
        ...

    def map(self, payload: RawPayload) -> HsdsObjects:
        """Map one source record to HSDS objects.

        Raise :class:`MapError` for a record that cannot be mapped rather than
        raising for the whole batch.
        """
        ...

    def native_id(self, payload: RawPayload) -> str:
        """Return the source's native record id for this payload.

        Used to derive the deterministic HSDS id (uuid5 of ``name`` + native id).
        """
        ...
