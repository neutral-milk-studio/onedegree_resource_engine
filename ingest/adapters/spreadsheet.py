"""Partner spreadsheet (CSV) adapter, driven by a confirmed column mapping.

A partner uploads a spreadsheet; ``ingest.mapping`` suggests which HSDS field each column
holds; a person confirms or edits the mapping; then this adapter maps every row through
the normal pipeline (raw saved first, validate, store or quarantine with reasons).

One column may hold several fields ("packed"), written ``Key: value | Key: value``. Keys
are matched with the same synonyms as headers. Unknown keys are kept as a note in the
service description, never dropped.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass

from contracts.adapter import HsdsObjects, MapError, RawPayload
from ingest.adapters.base import RawPage
from ingest.transformer.hsds import clean, hsds_id, source_tag
from maintenance.extractor.parse import DAY_CODES, find_phone, parse_hours_line, split_list

# Target fields a column can map to (value -> plain-language label).
TARGETS: dict[str, str] = {
    "organization_name": "Organization name",
    "service_name": "Service name",
    "description": "Service description",
    "phone": "Phone",
    "email": "Email",
    "website": "Website",
    "address_1": "Street address",
    "city": "City",
    "state": "State",
    "postal_code": "ZIP code",
    "hours": "Hours",
    "eligibility": "Who can get help",
    "languages": "Languages",
    "service_area": "Service area",
    "packed": "Several fields in one column",
    "ignore": "Don't import",
}

# Header (or packed key) words -> target. Checked in order; first match wins.
SYNONYMS: list[tuple[str, tuple[str, ...]]] = [
    ("organization_name", ("organization", "agency", "run by", "operated by", "provider", "org")),
    ("service_name", ("pantry name", "program", "service", "site name", "name")),
    ("description", ("description", "about", "details")),
    ("phone", ("phone", "tel", "telephone", "contact number", "call")),
    ("email", ("email", "e-mail")),
    ("website", ("web", "website", "url", "site")),
    ("address_1", ("street", "address", "addr")),
    ("city", ("city", "town")),
    ("state", ("state",)),
    ("postal_code", ("zip", "postal", "post code")),
    ("hours", ("hours", "open", "schedule", "when")),
    ("eligibility", ("eligib", "who can", "who qualifies", "requirements")),
    ("languages", ("language",)),
    ("service_area", ("area", "serves", "county served", "region")),
    ("packed", ("info", "notes", "other", "misc")),
]


def target_for(label: str) -> str | None:
    lowered = label.strip().lower()
    for target, words in SYNONYMS:
        if any(word == lowered for word in words):
            return target
    for target, words in SYNONYMS:
        if any(word in lowered for word in words):
            return target
    return None


def read_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    """Headers and rows. Handles a UTF-8 BOM and falls back to Latin-1."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("The file has no header row.")
    headers = [h.strip() for h in reader.fieldnames]
    if len(set(headers)) != len(headers):
        raise ValueError("Two columns have the same name; rename one and upload again.")
    rows = []
    for row in reader:
        values = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k is not None}
        if any(values.values()):
            rows.append(values)
    return headers, rows


def unpack(text: str) -> tuple[dict[str, str], list[str]]:
    """'Hours: Mon 9-5 | Languages: English' -> ({'hours': ..., 'languages': ...}, notes)."""
    fields: dict[str, str] = {}
    notes: list[str] = []
    for part in (p.strip() for p in re.split(r"\s*\|\s*", text or "")):
        if not part:
            continue
        key, sep, value = part.partition(":")
        target = target_for(key) if sep else None
        if target and target not in {"packed", "ignore"} and value.strip():
            fields.setdefault(target, value.strip())
        else:
            notes.append(part)
    return fields, notes


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def normalize_website(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not re.match(r"^https?://", value, re.I):
        value = f"https://{value}"
    return value


_PHONE_EXT = re.compile(r"(?:ext\.?|x|extension)\s*(\d+)\s*$", re.I)
_ORDINAL = re.compile(r"\b(\d(st|nd|rd|th)|first|second|third|fourth|last|every other)\b", re.I)


def phone_object(raw: str, phone_id: str, service_id: str) -> dict:
    number = find_phone(raw)
    ext = _PHONE_EXT.search(raw or "")
    return clean({
        "id": phone_id,
        "service_id": service_id,
        # Not a phone number at all: keep it as written so the quality rule quarantines it
        # with a clear reason, instead of silently dropping what the partner wrote.
        "number": number or raw,
        "extension": int(ext.group(1)) if ext and number else None,
        "type": "voice",
    })  # fmt: skip


def schedules(hours: str, key: str, service_id: str, source: str) -> list[tuple[str, dict]]:
    """Weekly hours become structured schedules; anything else is kept as text (never guessed)."""
    slots = [] if _ORDINAL.search(hours) else parse_hours_line(hours)
    if not slots:
        return [("schedule", {"id": hsds_id(source, "schedule", f"{key}:text"),
                              "service_id": service_id, "description": hours})]  # fmt: skip
    groups: dict[tuple[str, str], list[str]] = {}
    for day, opens, closes in slots:
        groups.setdefault((opens, closes), []).append(day)
    return [
        ("schedule", {
            "id": hsds_id(source, "schedule", f"{key}:{opens}-{closes}"),
            "service_id": service_id,
            "freq": "WEEKLY",
            "byday": ",".join(sorted(days, key=DAY_CODES.index)),
            "opens_at": opens,
            "closes_at": closes,
            "description": hours,
        })
        for (opens, closes), days in groups.items()
    ]  # fmt: skip


@dataclass(frozen=True)
class CsvPage(RawPage):
    def parse(self) -> dict:
        headers, rows = read_csv(self.content)
        return {"headers": headers, "rows": rows}


class SpreadsheetAdapter:
    """A partner spreadsheet with a confirmed mapping {column: target}."""

    def __init__(
        self,
        name: str,
        filename: str,
        content: bytes,
        mapping: dict[str, str],
        default_state: str = "CA",
    ) -> None:
        unknown = {t for t in mapping.values() if t not in TARGETS}
        if unknown:
            raise ValueError(f"unknown target field(s): {sorted(unknown)}")
        self.name = name
        self.filename = filename
        self.content = content
        self.mapping = mapping
        self.default_state = default_state

    # -- fetching -----------------------------------------------------------------------

    def fetch_pages(self, since: str | None = None) -> Iterator[RawPage]:
        yield CsvPage(name=self.filename, content=self.content)

    def records(self, page: dict) -> list[RawPayload]:
        return [{"_row": i, **row} for i, row in enumerate(page["rows"], start=2)]

    def fetch(self, since: str | None = None) -> Iterator[RawPayload]:
        for page in self.fetch_pages(since):
            yield from self.records(page.parse())

    # -- mapping ------------------------------------------------------------------------

    def fields(self, row: dict) -> tuple[dict[str, str], list[str]]:
        out: dict[str, str] = {}
        notes: list[str] = []
        for column, target in self.mapping.items():
            value = (row.get(column) or "").strip()
            if not value or target == "ignore":
                continue
            if target == "packed":
                packed, extra = unpack(value)
                for key, val in packed.items():
                    out.setdefault(key, val)
                notes += extra
            else:
                out.setdefault(target, value)
        return out, notes

    def native_id(self, payload: RawPayload) -> str:
        fields, _ = self.fields(payload)
        org = fields.get("organization_name") or fields.get("service_name") or ""
        svc = fields.get("service_name") or org
        if not org:
            return f"row-{payload.get('_row')}"
        return f"{slug(org)}/{slug(svc)}"

    def map_ordered(self, payload: RawPayload) -> list[tuple[str, dict]]:
        fields, notes = self.fields(payload)
        source = self.name
        org_name = fields.get("organization_name") or fields.get("service_name")
        service_name = fields.get("service_name") or fields.get("organization_name")
        if not org_name:
            raise ValueError(f"row {payload.get('_row')}: no organization or service name")
        native = self.native_id(payload)
        org_id = hsds_id(source, "organization", slug(org_name))
        svc_id = hsds_id(source, "service", native)
        description = " ".join(filter(None, [fields.get("description"), *notes])) or None
        out: list[tuple[str, dict]] = [
            ("organization", clean({
                "id": org_id,
                "name": org_name,
                # Same rule as ODRS: placeholder + flag rather than quarantine (decisions §5).
                "description": f"{org_name}. Description not available.",
                "website": normalize_website(fields.get("website")),
                **source_tag(source, slug(org_name), flags=["missing_information:description"]),
            })),
            ("service", clean({
                "id": svc_id,
                "organization_id": org_id,
                "name": service_name,
                "description": description,
                "status": "active",
                "email": fields.get("email"),
                "eligibility_description": fields.get("eligibility"),
                **source_tag(source, native, row=payload.get("_row")),
            })),
        ]  # fmt: skip
        if fields.get("phone"):
            out.append(
                ("phone", phone_object(fields["phone"], hsds_id(source, "phone", native), svc_id))
            )
        if fields.get("hours"):
            out += schedules(fields["hours"], native, svc_id, source)
        for name in split_list(fields.get("languages", "")):
            out.append(("language", {"id": hsds_id(source, "language", f"{native}:{name}"),
                                     "service_id": svc_id, "name": name}))  # fmt: skip
        for name in split_list(fields.get("service_area", "")):
            out.append(("service_area", {"id": hsds_id(source, "service_area", f"{native}:{name}"),
                                         "service_id": svc_id, "name": name}))  # fmt: skip
        if any(fields.get(k) for k in ("address_1", "city", "postal_code")):
            loc_id = hsds_id(source, "location", native)
            out.append(("location", {"id": loc_id, "location_type": "physical",
                                     "organization_id": org_id, "name": service_name}))  # fmt: skip
            # An incomplete address (e.g. no street) fails validation and is quarantined with
            # its reason; the location and the rest of the record are still stored.
            out.append(("address", clean({
                "id": hsds_id(source, "address", native),
                "location_id": loc_id,
                "address_1": fields.get("address_1"),
                "city": fields.get("city"),
                "state_province": fields.get("state") or self.default_state,
                "postal_code": fields.get("postal_code"),
                "country": "US",
                "address_type": "physical",
            })))  # fmt: skip
            sal_id = hsds_id(source, "service_at_location", native)
            out.append(("service_at_location",
                        {"id": sal_id, "service_id": svc_id, "location_id": loc_id}))  # fmt: skip
        return out

    def map(self, payload: RawPayload) -> HsdsObjects:
        try:
            objects = self.map_ordered(payload)
        except Exception as exc:  # noqa: BLE001
            raise MapError(str(payload.get("_row", "?")), repr(exc)) from exc
        grouped: HsdsObjects = {}
        for kind, obj in objects:
            grouped.setdefault(kind, []).append(obj)
        return grouped
