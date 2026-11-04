"""The production FindingsSink: a finding becomes a change request a person decides on.

Verified results propose nothing, so they only update the check result. The comparator's proposals
are text. HSDS holds phones, languages, areas and hours as lists of objects, so those are built
here. A value that still can't be stored as valid HSDS is flagged for the reviewer to edit.
"""

from __future__ import annotations

import re
from dataclasses import asdict

from contracts.findings import Category, Finding, FindingRejected, finding_problems
from shared.uuid_util import hsds_id

_SPLIT = re.compile(r"\s*[,;]\s*|\s+or\s+|\s+and\s+")
_LABEL = {
    Category.DISCREPANCY: "The website disagrees with the record: {field}",
    Category.MISSING_INFORMATION: "The record is missing: {field}",
}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)[-10:]


def _object_id(service_id: str, field: str, value: str) -> str:
    return hsds_id("maintenance", f"{service_id}:{field}:{value}")


def structured_value(field: str, value, current, service_id: str):
    """Turn the comparator's text into the shape HSDS stores for this field."""
    if not isinstance(value, str):
        return value
    existing = list(current) if isinstance(current, list) else []
    if field == "phones":
        known = {_digits(item.get("number", "")) for item in existing if isinstance(item, dict)}
        added = []
        for part in _SPLIT.split(value):
            if part and _digits(part) not in known:
                known.add(_digits(part))
                added.append(
                    {
                        "id": _object_id(service_id, field, part),
                        "service_id": service_id,
                        "number": part,
                        "type": "voice",
                    }
                )
        return existing + added
    if field in ("languages", "service_areas"):
        keep = [] if field == "service_areas" else existing
        return keep + [
            {"id": _object_id(service_id, field, part), "service_id": service_id, "name": part}
            for part in _SPLIT.split(value)
            if part
        ]
    if field == "schedules":
        # Free text can't become weekly rules safely, so it stays a note for the reviewer to see.
        note = {
            "id": _object_id(service_id, field, value),
            "service_id": service_id,
            "notes": value,
        }
        return existing + [note]
    return value


class ChangeRequestSink:
    def __init__(self, store, proposals):
        self.store, self.proposals = store, proposals

    def save(self, finding: Finding) -> str:
        if problems := finding_problems(finding):
            raise FindingRejected("; ".join(problems))
        if finding.category is Category.VERIFIED:
            return finding.id
        row = self.store.get(finding.service_id)
        if row is None:
            raise FindingRejected(f"service {finding.service_id} is not stored")

        patch = {}
        if finding.field and finding.proposed_value is not None:
            current = row["data"].get(finding.field)
            proposed = structured_value(
                finding.field, finding.proposed_value, current, finding.service_id
            )
            patch[finding.field] = {"current": current, "proposed": proposed}

        label = _LABEL.get(finding.category)
        title = (
            label.format(field=finding.field.replace("_", " "))
            if label and finding.field
            else finding.evidence[0].snippet[:200]
        )
        if patch and self.store.validate_patch(
            finding.service_id, {f: v["proposed"] for f, v in patch.items()}
        ):
            title += " (edit before applying: not valid HSDS as proposed)"
        return self.proposals.create(
            resource_id=row["id"],
            resource_type=row["type"],
            org_id=row["org_id"],
            kind="ai_finding",
            proposed_by_type="ai",
            proposed_by="ai-maintenance",
            patch=patch,
            evidence=[{**asdict(item), "field": finding.field} for item in finding.evidence],
            category=finding.category.value,
            title=title,
        )
