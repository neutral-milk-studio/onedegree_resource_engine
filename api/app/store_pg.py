"""PostgreSQL HsdsStore implementation — STUB (task 14).

Implements the HsdsStore contract over Postgres (Open Referral schema), with the
id_map, idempotent upsert, metadata history, quarantine, and counts. Round-trips
to valid HSDS. Spec: Requirements 11.3, 11.4, 12.2, 12.3, 13.4, 26.2.
"""

from __future__ import annotations

from typing import Any

from contracts.store import IngestCounts, Page, ServiceFilter


class PgHsdsStore:
    """Concrete HsdsStore over PostgreSQL (satisfies contracts.store.HsdsStore)."""

    def get_service(self, service_id: str, source: str | None = None) -> dict[str, Any] | None:
        raise NotImplementedError("task 14.1: get_service")

    def list_services(self, filt: ServiceFilter, page: int, per_page: int) -> Page:
        raise NotImplementedError("task 14.1 / 26.3: list_services with Page")

    def upsert_record(self, hsds_objects: dict[str, Any], source: str, native_id: str) -> str:
        raise NotImplementedError("task 14.1: idempotent upsert by uuid5 id")

    def write_metadata(
        self,
        service_id: str,
        field_name: str,
        previous_value: str,
        replacement_value: str,
        updated_by: str,
    ) -> None:
        raise NotImplementedError("task 14.3: append HSDS metadata history row")

    def quarantine(self, source: str, native_id: str, raw_ref: str, reason: str) -> None:
        raise NotImplementedError("task 15.1: quarantine with reason")

    def counts(self, source: str) -> IngestCounts:
        raise NotImplementedError("task 15.1: received/stored/quarantined counts")
