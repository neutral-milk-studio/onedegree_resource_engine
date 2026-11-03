"""Mock NDP source A adapter — STUB (filled by the Data Ingest stream, task 11.1).

Implements the SourceAdapter contract. Spec: Requirements 8.1, 8.2, 8.4, 13.1.
"""

from __future__ import annotations

from typing import Iterator

from contracts.adapter import HsdsObjects, RawPayload


class NdpAAdapter:
    name = "ndp_a"

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        raise NotImplementedError("task 11.1: fetch raw NDP source A payloads")

    def map(self, payload: RawPayload) -> HsdsObjects:
        raise NotImplementedError("task 11.1: map NDP source A record -> HSDS objects")

    def native_id(self, payload: RawPayload) -> str:
        raise NotImplementedError("task 11.1: return NDP source A native id")
