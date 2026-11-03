"""Mock NDP source B adapter (structurally different) — STUB (task 11.2).

Proves source-agnostic ingest: a different NDP layout behind the same interface.
Spec: Requirements 8.3, 8.4.
"""

from __future__ import annotations

from typing import Iterator

from contracts.adapter import HsdsObjects, RawPayload


class NdpBAdapter:
    name = "ndp_b"

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        raise NotImplementedError("task 11.2: fetch raw NDP source B payloads")

    def map(self, payload: RawPayload) -> HsdsObjects:
        raise NotImplementedError("task 11.2: map NDP source B record -> HSDS objects")

    def native_id(self, payload: RawPayload) -> str:
        raise NotImplementedError("task 11.2: return NDP source B native id")
