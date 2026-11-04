"""One HSDS rulebook for the whole system: the ingest transform's validate().

It checks the pinned v3.2.3 schemas in contracts/hsds/schema, the tabular required fields, and the
Resource Engine quality rules, so a record is judged the same at ingest and on every later edit.
"""

from __future__ import annotations

from ingest.pipeline.transform.hsds import HSDS_VERSION, validate


class HsdsValidator:
    def __init__(self) -> None:
        self.version = HSDS_VERSION

    def errors(self, kind: str, data: dict) -> list[str]:
        return validate(kind, data)

    def engine(self) -> str:
        return f"jsonschema, official HSDS v{self.version}"
