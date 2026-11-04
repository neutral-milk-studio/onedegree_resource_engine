"""Apply a reviewed value to a nested HSDS service document.

Used when a specialist applies a finding, and again after every re-ingest to re-apply their
saved changes (``Service.overrides``). The caller validates the result as HSDS before saving.
"""

from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

from maintenance.extractor.parse import phone_digits

NAMESPACE = uuid.UUID("0b8f4f4e-6a51-5d6b-9c0e-2a7d4b1e9f31")
DEFAULT_TIMEZONE = os.environ.get("RE_DEFAULT_TIMEZONE", "America/Los_Angeles")
STATUSES = {"active", "inactive", "defunct", "temporarily closed"}
LANGUAGE_CODES = {
    "english": "en", "spanish": "es", "chinese": "zh", "cantonese": "yue", "mandarin": "cmn",
    "tagalog": "tl", "vietnamese": "vi", "russian": "ru", "korean": "ko", "arabic": "ar",
}  # fmt: skip


class ApplyError(ValueError):
    """The value can't be applied to this field (wrong shape, unknown field)."""


def _id(service_id: str, *parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join([service_id, *parts])))


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [v for v in (part.strip() for part in value.split(",")) if v]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ApplyError("expected a list of names")
    return [v.strip() for v in value]


def apply_change(doc: dict, field: str, value: Any, current: Any = None) -> dict:
    """Return a copy of ``doc`` with ``field`` set to ``value``."""
    doc = copy.deepcopy(doc)
    sid = doc["id"]
    if field == "phone":
        if not isinstance(value, str) or not phone_digits(value):
            raise ApplyError("phone must be a phone number")
        old = phone_digits(current or "")
        changed = False
        for phone in _phones(doc):
            if old and phone_digits(phone.get("number", "")) == old:
                phone["number"], changed = value, True
        if not changed:
            doc.setdefault("phones", []).insert(
                0, {"id": _id(sid, "phone"), "number": value, "service_id": sid}
            )
    elif field == "hours":
        if not isinstance(value, list) or not value:
            raise ApplyError("hours must be a list of {byday, opens_at, closes_at}")
        schedules = []
        for i, slot in enumerate(value):
            if not all(isinstance(slot.get(k), str) for k in ("byday", "opens_at", "closes_at")):
                raise ApplyError("each hours entry needs byday, opens_at and closes_at")
            schedules.append({
                "id": _id(sid, "hours", str(i)),
                "service_id": sid,
                "freq": "WEEKLY",
                "byday": slot["byday"],
                "opens_at": slot["opens_at"][:5],
                "closes_at": slot["closes_at"][:5],
                "x_resource_engine": {"timezone": DEFAULT_TIMEZONE},
            })  # fmt: skip
        doc["schedules"] = schedules
    elif field == "languages":
        doc["languages"] = [
            {"id": _id(sid, "language", name.casefold()), "service_id": sid, "name": name}
            | (
                {"code": LANGUAGE_CODES[name.casefold()]}
                if name.casefold() in LANGUAGE_CODES
                else {}
            )
            for name in _as_list(value)
        ]
    elif field == "service_area":
        doc["service_areas"] = [
            {"id": _id(sid, "area", name.casefold()), "service_id": sid, "name": name}
            for name in _as_list(value)
        ]
    elif field == "eligibility":
        if not isinstance(value, str) or not value.strip():
            raise ApplyError("eligibility must be text")
        doc["eligibility_description"] = value.strip()
    elif field == "accessibility":
        if not isinstance(value, str) or not value.strip():
            raise ApplyError("accessibility must be text")
        location = next(
            (s["location"] for s in doc.get("service_at_locations") or [] if s.get("location")),
            None,
        )
        if location is None:
            raise ApplyError("this service has no location to describe")
        items = location.setdefault("accessibility", [])
        if items:
            items[0]["description"] = value.strip()
        else:
            items.append({"id": _id(sid, "access"), "location_id": location["id"],
                          "description": value.strip()})  # fmt: skip
    elif field == "status":
        if value not in STATUSES:
            raise ApplyError(f"status must be one of {sorted(STATUSES)}")
        doc["status"] = value
    else:
        raise ApplyError(f"don't know how to apply field {field!r}")
    return doc


def _phones(doc: dict) -> list[dict]:
    phones = list(doc.get("phones") or [])
    for sal in doc.get("service_at_locations") or []:
        phones += sal.get("phones") or []
        phones += (sal.get("location") or {}).get("phones") or []
    phones += (doc.get("organization") or {}).get("phones") or []
    return phones


def add_metadata(doc: dict, field: str, previous: Any, replacement: Any, updated_by: str) -> dict:
    """Append the HSDS change-history entry required for every applied change."""

    def text(value: Any) -> str:
        if value is None or value == "":
            return "(none)"
        return value if isinstance(value, str) else json.dumps(value)

    now = datetime.now(UTC)
    entry = {
        "id": str(uuid.uuid4()),
        "resource_id": doc["id"],
        "resource_type": "service",
        "last_action_date": now.date().isoformat(),
        "last_action_type": "update",
        "field_name": field,
        "previous_value": text(previous),
        "replacement_value": text(replacement),
        "updated_by": updated_by,
    }
    doc.setdefault("metadata", []).append(entry)
    return doc
