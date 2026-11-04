"""Expose a mapper module (odrs, ndp) as a contracts.adapter.SourceAdapter.

The mappers stay plain modules (records / native_id / map returning (kind, obj) pairs);
this wrapper gives them the shared contract the rest of the system codes against.
"""

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

from contracts.adapter import HsdsObjects, MapError, RawPayload

from .hsds import KIND_ORDER


def group(pairs: list[tuple[str, dict]]) -> HsdsObjects:
    """[(kind, obj), ...] -> {kind: [obj, ...]}, keeping each kind's order."""
    out: HsdsObjects = {}
    for kind, obj in pairs:
        out.setdefault(kind, []).append(obj)
    return out


def flatten(objects: HsdsObjects) -> list[tuple[str, dict]]:
    """{kind: [obj, ...]} -> [(kind, obj), ...] with parents before children."""
    rank = {kind: i for i, kind in enumerate(KIND_ORDER)}
    kinds = sorted(objects, key=lambda k: rank.get(k, len(rank)))
    return [(kind, obj) for kind in kinds for obj in objects[kind]]


class MapperAdapter:
    """SourceAdapter over saved API pages (there is no live fetch for the hackathon).

    Each record in a page is one RawPayload.
    """

    def __init__(self, mapper: ModuleType, pages: Iterable[Path | dict] = ()):
        self.mapper = mapper
        self.name: str = mapper.SOURCE
        self.pages = list(pages)

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        # `since` is ignored: saved pages carry no watermark.
        for page in self.pages:
            if isinstance(page, Path):
                page = json.loads(page.read_text())
            yield from self.mapper.records(page)

    def native_id(self, payload: RawPayload) -> str:
        return self.mapper.native_id(payload)

    def map(self, payload: RawPayload) -> HsdsObjects:
        try:
            return group(self.mapper.map(payload))
        except Exception as exc:  # unexpected shape or mapper bug: fail this record only
            raise MapError(str(payload.get("id")), repr(exc)) from exc
