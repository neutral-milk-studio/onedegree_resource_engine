"""Spreadsheet adapter — STUB (stretch, task 17).

Ingests two structurally different spreadsheet layouts through the same
interface. Spec: Requirement 17.1.
"""

from __future__ import annotations

from collections.abc import Iterator

from contracts.adapter import HsdsObjects, RawPayload


class SpreadsheetAdapter:
    name = "spreadsheet"

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        raise NotImplementedError("task 17: read spreadsheet rows as raw payloads")

    def map(self, payload: RawPayload) -> HsdsObjects:
        raise NotImplementedError("task 17: map a spreadsheet row -> HSDS objects")

    def native_id(self, payload: RawPayload) -> str:
        raise NotImplementedError("task 17: return the spreadsheet row native id")
