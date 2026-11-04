"""Adapter wrapper: turns a mapper module into a :class:`contracts.adapter.SourceAdapter`.

A mapper module (``ingest/adapters/odrs.py``, ``ndp.py``, ...) provides four things:

- ``SOURCE``: the stable source name, used in every deterministic id
- ``records(page) -> list[dict]``: pull the records out of one saved page
- ``native_id(record) -> str``
- ``map(record) -> list[(kind, hsds_object)]``, parents before children

:class:`ModuleAdapter` adds fetching (pages read from files named in
``config/sources.yaml``) so a new source is one mapper module plus one config entry.
"""

from __future__ import annotations

import glob
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from contracts.adapter import HsdsObjects, MapError, RawPayload

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RawPage:
    """One fetched payload, exactly as received (bytes), plus a name for the raw store."""

    name: str
    content: bytes

    def parse(self) -> dict:
        return json.loads(self.content)


class ModuleAdapter:
    """A source adapter built from a mapper module and a list of input files."""

    def __init__(self, module: ModuleType, inputs: list[str], name: str | None = None) -> None:
        self.module = module
        self.name: str = name or module.SOURCE
        self.inputs = inputs

    # -- fetching -------------------------------------------------------------------------

    def input_paths(self) -> list[Path]:
        paths: list[Path] = []
        for pattern in self.inputs:
            absolute = pattern if Path(pattern).is_absolute() else str(REPO_ROOT / pattern)
            paths += [Path(p) for p in sorted(glob.glob(absolute))]
        return paths

    def fetch_pages(self, since: str | None = None) -> Iterator[RawPage]:
        """Yield each source page as received. ``since`` is reserved for incremental APIs."""
        paths = self.input_paths()
        if not paths:
            raise FileNotFoundError(
                f"source {self.name!r}: no input files match {self.inputs} "
                f"(relative to {REPO_ROOT})"
            )
        for path in paths:
            yield RawPage(name=path.name, content=path.read_bytes())

    def records(self, page: dict) -> list[RawPayload]:
        return self.module.records(page)

    # -- contracts.adapter.SourceAdapter --------------------------------------------------

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        for page in self.fetch_pages(since):
            yield from self.records(page.parse())

    def native_id(self, payload: RawPayload) -> str:
        return self.module.native_id(payload)

    def map_ordered(self, payload: RawPayload) -> list[tuple[str, dict]]:
        """The mapper's own output: (kind, object) pairs with parents first."""
        return self.module.map(payload)

    def map(self, payload: RawPayload) -> HsdsObjects:
        try:
            objects = self.map_ordered(payload)
        except Exception as exc:  # noqa: BLE001 - surfaced as a per-record MapError
            raise MapError(str(payload.get("id", "?")), repr(exc)) from exc
        grouped: HsdsObjects = {}
        for kind, obj in objects:
            grouped.setdefault(kind, []).append(obj)
        return grouped
