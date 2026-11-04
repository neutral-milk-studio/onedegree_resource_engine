"""ODRS (One Degree /v1/opportunities) -> HSDS mapper.

One ODRS opportunity becomes one HSDS service plus its organization, locations,
addresses, phones, schedules and service_at_location links. MVP scope: core
objects only. Taxonomy (tags/categories/areas), attribute flags, languages and
required documents stay in the raw record and are mapped in a later step.
"""

from .hsds import clean, hsds_id, source_tag, split_phone, to_number

SOURCE = "odrs"

DAYS = [
    ("monday", "MO"), ("tuesday", "TU"), ("wednesday", "WE"), ("thursday", "TH"),
    ("friday", "FR"), ("saturday", "SA"), ("sunday", "SU"),
]


def records(page: dict) -> list[dict]:
    """Pull the records out of one saved API page (the shape of BayesExport_*.json)."""
    return page.get("opportunities") or []


def native_id(record: dict) -> str:
    return str(record["id"])


def map(record: dict) -> list[tuple[str, dict]]:
    """Translate one opportunity into a flat list of (kind, HSDS object) linked by id."""
    out: list[tuple[str, dict]] = []
    opp_id = record["id"]
    props = record.get("properties") or {}

    org = record["organization"]
    org_id = hsds_id(SOURCE, "organization", org["id"])
    out.append(("organization", _organization(org, org_id)))

    svc_id = hsds_id(SOURCE, "service", opp_id)
    out.append(("service", clean({
        "id": svc_id,
        "organization_id": org_id,
        "name": record["title"],
        "description": record.get("description"),
        # Decision: a set unavailable_at means the opportunity is gone -> inactive.
        "status": "inactive" if record.get("unavailable_at") else "active",
        "url": props.get("action-signup-url"),
        "email": _primary_email(record.get("emails")),
        "eligibility_description": props.get("elig-description"),
        "minimum_age": to_number(props.get("elig-age-or-over")),
        "maximum_age": to_number(props.get("elig-age-or-under")),
        "fees_description": _fees(props),
        "application_process": _application_process(record.get("access_instructions")),
        "assured_date": (record.get("last_verified_at") or "")[:10],
        "last_modified": record.get("updated_at"),
        **source_tag(SOURCE, opp_id),
    })))

    for phone in record.get("phones") or []:
        out.append(("phone", _phone(phone, service_id=svc_id)))
    out += _schedules(record.get("schedule"), f"{opp_id}", service_id=svc_id)

    for loc in record.get("locations") or []:
        loc_id = hsds_id(SOURCE, "location", loc["id"])
        sal_id = hsds_id(SOURCE, "service_at_location", f"{opp_id}:{loc['id']}")
        out.append(("location", clean({
            "id": loc_id,
            "location_type": "physical",
            "organization_id": org_id,
            "name": loc.get("name"),
            "latitude": loc.get("lat"),
            "longitude": loc.get("long"),
            **source_tag(SOURCE, loc["id"]),
        })))
        out.append(("address", clean({
            "id": hsds_id(SOURCE, "address", loc["id"]),
            "location_id": loc_id,
            "address_1": loc.get("address"),
            "address_2": loc.get("unit"),
            "city": loc.get("city"),
            "state_province": loc.get("state"),
            "postal_code": loc.get("zip_code"),
            "country": "US",
            "address_type": "physical",
        })))
        for phone in loc.get("phones") or []:
            out.append(("phone", _phone(phone, location_id=loc_id)))
        out.append(("service_at_location", clean({
            "id": sal_id,
            "service_id": svc_id,
            "location_id": loc_id,
        })))
        # The location's hours here are specific to this opportunity, so they hang off the link.
        out += _schedules(loc.get("schedule"), f"{opp_id}:{loc['id']}", service_at_location_id=sal_id)

    return out


def _organization(org: dict, org_id: str) -> dict:
    # ODRS returns no organization description, and HSDS requires one (section 5.1.1).
    # MVP decision: plain fallback, flagged as missing information.
    return clean({
        "id": org_id,
        "name": org["name"],
        "description": f"{org['name']}. Description not available.",
        "website": org.get("website"),
        **source_tag(SOURCE, org["id"], flags=["missing_information:description"]),
    })


def _phone(phone: dict, **parent) -> dict:
    number, extension = split_phone(phone.get("digits"))
    label = phone.get("phone_type") or ""
    return clean({
        "id": hsds_id(SOURCE, "phone", phone["id"]),
        "number": number,
        "extension": extension,
        "type": "textphone" if any(t in label.upper() for t in ("TDD", "TTY")) else "voice",
        "description": label,  # ODRS phone_type is a free-text label, not a type
        **parent,
    })


def _schedules(schedule: dict | None, key: str, **parent) -> list[tuple[str, dict]]:
    """ODRS flat weekly hours -> one HSDS schedule per distinct (opens, closes), days grouped.

    Times are kept as ODRS gives them: America/Los_Angeles wall-clock HH:MM, no UTC
    offset (section 4.8 decision; a fixed offset would be wrong half the year).
    """
    if not schedule:
        return []
    groups: dict[tuple[str, str], list[str]] = {}
    for day, code in DAYS:
        opens, closes = schedule.get(f"{day}_start"), schedule.get(f"{day}_end")
        if opens and closes:
            groups.setdefault((opens, closes), []).append(code)
    notes = schedule.get("notes")

    out = [
        ("schedule", clean({
            "id": hsds_id(SOURCE, "schedule", f"{key}:{opens}-{closes}"),
            "freq": "WEEKLY",
            "byday": ",".join(days),
            "opens_at": opens,
            "closes_at": closes,
            "notes": notes,
            **parent,
        }))
        for (opens, closes), days in groups.items()
    ]
    if not out and notes:
        out.append(("schedule", clean({
            "id": hsds_id(SOURCE, "schedule", f"{key}:notes"),
            "notes": notes,
            **parent,
        })))
    return out


def _primary_email(emails: list[dict] | None) -> str | None:
    if not emails:
        return None
    primary = next((e for e in emails if e.get("is_primary")), emails[0])
    return primary.get("email")


def _fees(props: dict) -> str | None:
    parts = []
    if props.get("cost-free"):
        parts.append("Free.")
    if props.get("cost-pay"):
        parts.append("Fees apply.")
    if props.get("cost-note"):
        parts.append(props["cost-note"])
    return " ".join(parts) or None


def _application_process(instructions: list[dict] | None) -> str | None:
    steps = []
    for item in instructions or []:
        text = (item.get("instructions") or "").strip()
        value = (item.get("access_value") or "").strip()
        if text and value and value not in text:
            steps.append(f"{text} ({value})")
        elif text or value:
            steps.append(text or value)
    return "\n".join(steps) or None
