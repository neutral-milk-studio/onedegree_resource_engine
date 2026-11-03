"""HSDS store contract — the read/write surface the stores expose.

Whatever the internal schema, the store must round-trip to valid HSDS JSON.
Operational data (findings, review state, logs) lives in separate tables/stores,
never mixed into the HSDS records.

Spec: Requirements 1.1, 11.3, 12.2, 13.4, 24.1, 26.2, 30.1, 30.2, 33.3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Page:
    """HSDS Page wrapper for list responses."""

    total_items: int
    total_pages: int
    page_number: int
    size: int
    first_page: bool
    last_page: bool
    empty: bool
    contents: list[dict[str, Any]] = field(default_factory=list)  # HSDS service records


@dataclass
class ServiceFilter:
    """Filters for list_services. Supplied filters combine with AND."""

    modified_after: str | None = None
    organization_id: str | None = None
    taxonomy_term_id: str | None = None
    taxonomy_id: str | None = None
    source: str | None = None   # per-source key scoping (Req 33.3)
    search: str | None = None   # resolved against the OpenSearch index (Req 30.8, P5)


@dataclass
class IngestCounts:
    """Per-source ingest run counts.

    Invariant: ``received == stored + quarantined``. A record that fails is
    quarantined (never dropped, never stored invalid).
    """

    received: int
    stored: int
    quarantined: int

    def is_reconciled(self) -> bool:
        return self.received == self.stored + self.quarantined


@runtime_checkable
class HsdsStore(Protocol):
    """Read/write operations on the HSDS store. Round-trips to valid HSDS."""

    def get_service(self, service_id: str, source: str | None = None) -> dict[str, Any] | None:
        """Return one fully nested HSDS service, or None if not found.

        When ``source`` is set (a source-scoped key), a service belonging to a
        different source is treated as not found (no disclosure).
        """
        ...

    def list_services(self, filt: ServiceFilter, page: int, per_page: int) -> Page:
        """Return a paginated Page of HSDS services matching all supplied filters."""
        ...

    def upsert_record(self, hsds_objects: dict[str, Any], source: str, native_id: str) -> str:
        """Upsert by deterministic UUIDv5 id (source + native_id). Idempotent.

        Returns the HSDS id. Re-ingesting the same source updates rather than
        duplicating (Req 12.2).
        """
        ...

    def write_metadata(
        self,
        service_id: str,
        field_name: str,
        previous_value: str,
        replacement_value: str,
        updated_by: str,
    ) -> None:
        """Append one HSDS metadata change-history entry for an applied change."""
        ...

    def quarantine(self, source: str, native_id: str, raw_ref: str, reason: str) -> None:
        """Hold an unconvertible record with its reason and a raw-store reference."""
        ...

    def counts(self, source: str) -> IngestCounts:
        """Return received/stored/quarantined counts for a source."""
        ...
