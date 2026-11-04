"""Build the nested HSDS service document from the flat objects the mappers produce.

Storage keeps local wall-clock opening hours plus an IANA time zone
(``schedule.x_resource_engine.timezone``) and adds the UTC offset only when a record is
served (docs/decisions.md section 6), because a stored offset goes stale at the next
daylight-saving change. :func:`served` produces that outgoing form.
"""

from __future__ import annotations

import copy
import os
from collections import defaultdict
from collections.abc import Iterable
from datetime import date
from typing import Any, Protocol

from shared.hsds_time import to_hsds_time

DEFAULT_TIMEZONE = os.environ.get("RE_DEFAULT_TIMEZONE", "America/Los_Angeles")
EXT = "x_resource_engine"

# Parent link fields worth indexing, per the HSDS tabular foreign keys plus attribute.link_id.
LINK_FIELDS = ("organization_id", "service_id", "location_id", "service_at_location_id", "link_id")


class ObjectIndex(Protocol):
    def get(self, kind: str, object_id: str) -> dict | None: ...

    def children(self, kind: str, field: str, parent_id: str) -> list[dict]: ...


class MemoryIndex:
    """Index over a list of (kind, object) pairs, e.g. one record's mapped batch."""

    def __init__(self, objects: Iterable[tuple[str, dict]]) -> None:
        self._by_id: dict[tuple[str, str], dict] = {}
        self._children: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for kind, obj in objects:
            self._by_id[(kind, obj["id"])] = obj
            for field in LINK_FIELDS:
                if obj.get(field):
                    self._children[(kind, field, obj[field])].append(obj)

    def get(self, kind: str, object_id: str) -> dict | None:
        return self._by_id.get((kind, object_id))

    def children(self, kind: str, field: str, parent_id: str) -> list[dict]:
        return self._children.get((kind, field, parent_id), [])


def _with_timezone(schedules: list[dict]) -> list[dict]:
    out = []
    for sched in schedules:
        sched = copy.deepcopy(sched)
        if "opens_at" in sched or "closes_at" in sched:
            ext = sched.setdefault(EXT, {})
            ext.setdefault("timezone", DEFAULT_TIMEZONE)
        out.append(sched)
    return out


def _sorted(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda o: o["id"])


def _location(index: ObjectIndex, location_id: str) -> dict | None:
    loc = index.get("location", location_id)
    if loc is None:
        return None
    doc = copy.deepcopy(loc)
    for field, kind in (
        ("addresses", "address"),
        ("phones", "phone"),
        ("accessibility", "accessibility"),
        ("languages", "language"),
    ):
        if items := _sorted(index.children(kind, "location_id", location_id)):
            doc[field] = copy.deepcopy(items)
    if schedules := _sorted(index.children("schedule", "location_id", location_id)):
        doc["schedules"] = _with_timezone(schedules)
    return doc


def _attributes(index: ObjectIndex, link_id: str) -> list[dict]:
    out = []
    for attr in _sorted(index.children("attribute", "link_id", link_id)):
        attr = copy.deepcopy(attr)
        term = index.get("taxonomy_term", attr.get("taxonomy_term_id", ""))
        if term:
            attr["taxonomy_term"] = copy.deepcopy(term)
        out.append(attr)
    return out


def assemble_service(index: ObjectIndex, service: dict) -> dict:
    """Return the fully nested HSDS service for ``service`` (a flat service object)."""
    sid = service["id"]
    doc = copy.deepcopy(service)

    org = index.get("organization", service.get("organization_id", ""))
    if org:
        org_doc = copy.deepcopy(org)
        if phones := _sorted(index.children("phone", "organization_id", org["id"])):
            org_doc["phones"] = copy.deepcopy(phones)
        doc["organization"] = org_doc

    for field, kind in (
        ("phones", "phone"),
        ("service_areas", "service_area"),
        ("languages", "language"),
    ):
        if items := _sorted(index.children(kind, "service_id", sid)):
            doc[field] = copy.deepcopy(items)
    if schedules := _sorted(index.children("schedule", "service_id", sid)):
        doc["schedules"] = _with_timezone(schedules)
    if attributes := _attributes(index, sid):
        doc["attributes"] = attributes

    sals = []
    for sal in _sorted(index.children("service_at_location", "service_id", sid)):
        sal_doc = copy.deepcopy(sal)
        if location := _location(index, sal.get("location_id", "")):
            sal_doc["location"] = location
        if phones := _sorted(index.children("phone", "service_at_location_id", sal["id"])):
            sal_doc["phones"] = copy.deepcopy(phones)
        if schedules := _sorted(index.children("schedule", "service_at_location_id", sal["id"])):
            sal_doc["schedules"] = _with_timezone(schedules)
        sals.append(sal_doc)
    if sals:
        doc["service_at_locations"] = sals
    return doc


def _offset_schedules(value: Any, on: date) -> None:
    if isinstance(value, dict):
        tz = (value.get(EXT) or {}).get("timezone")
        if tz:
            day = on
            if value.get("valid_from"):
                try:
                    day = date.fromisoformat(value["valid_from"][:10])
                except ValueError:
                    day = on
            for field in ("opens_at", "closes_at"):
                if isinstance(value.get(field), str) and len(value[field]) <= 8:
                    try:
                        value[field] = to_hsds_time(value[field], tz, day)
                    except ValueError:
                        pass  # left as-is; served-form validation reports it
        for child in value.values():
            _offset_schedules(child, on)
    elif isinstance(value, list):
        for child in value:
            _offset_schedules(child, on)


def served(document: dict, on: date | None = None, include_metadata: bool = True) -> dict:
    """The outgoing HSDS form: opening hours carry the UTC offset in effect on ``on``."""
    doc = copy.deepcopy(document)
    _offset_schedules(doc, on or date.today())
    if not include_metadata:
        doc.pop("metadata", None)
    return doc


def key_columns(doc: dict) -> dict[str, Any]:
    """Columns copied out of a nested service for filtering and the dashboard."""
    org = doc.get("organization") or {}
    phones = doc.get("phones") or org.get("phones") or []
    address: dict = {}
    for sal in doc.get("service_at_locations") or []:
        for addr in (sal.get("location") or {}).get("addresses") or []:
            if addr.get("address_type") == "physical" or not address:
                address = addr
        if not phones:
            phones = sal.get("phones") or (sal.get("location") or {}).get("phones") or []
    from search.indexer.build_index import search_text

    return {
        "search_text": search_text(doc),
        "organization_id": doc.get("organization_id"),
        "organization_name": org.get("name"),
        "name": doc["name"],
        "status": doc["status"],
        "website": org.get("website") or doc.get("url"),
        "phone": phones[0]["number"] if phones else None,
        "city": address.get("city"),
        "postal_code": address.get("postal_code"),
        "last_modified": doc.get("last_modified"),
        "assured_date": doc.get("assured_date"),
    }
