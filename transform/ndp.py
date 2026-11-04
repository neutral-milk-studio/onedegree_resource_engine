"""211 NDP (National Data Platform) -> HSDS mapper.

One NDP record is one organization with its services, locations, phones and the
links between them. NDP looks like HSDS but isn't (section 5.2): ids aren't UUIDs,
fields are camelCase, status sits in meta, fees and languages are objects, and
schedules use a different shape. Unlike ODRS, NDP gives an organization
description and a multi-level taxonomy, so this mapper also fills taxonomy,
taxonomy_term and attribute.

There is no NDP API for the hackathon; input is the sample pages in data/samples/NDP/.
"""

from .hsds import clean, hsds_id, source_tag, split_phone, strip_html, to_number

SOURCE = "ndp"

DAYS = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}
DAY_ORDER = list(DAYS.values())
ADDRESS_TYPES = {"physical": "physical", "mailing": "postal"}
PHONE_TYPES = {"fax": "fax", "tty": "textphone"}  # main / other / missing -> voice
COUNTRIES = {"United States": "US"}
LANGUAGE_CODES = {  # ISO 639 codes for the names seen in the samples; others keep just the name
    "English": "en", "Spanish": "es", "Chinese": "zh", "Cantonese": "yue",
    "Mandarin": "cmn", "Tagalog": "tl", "Vietnamese": "vi", "Russian": "ru",
}

TAXONOMY_ID = hsds_id(SOURCE, "taxonomy", "211")
TAXONOMY = {
    "id": TAXONOMY_ID,
    "name": "211 Taxonomy",
    "description": "Service categories supplied with 211 National Data Platform (NDP) records, "
                   "e.g. code HD-8200 'School Districts'.",
}


def records(page: dict) -> list[dict]:
    """Pull the records out of one saved page (the shape of national-data-platform-sample-*.json)."""
    return page.get("data") or []


def native_id(record: dict) -> str:
    return str(record["id"])


def map(record: dict) -> list[tuple[str, dict]]:
    """Translate one NDP organization record into a flat list of (kind, HSDS object)."""
    out: list[tuple[str, dict]] = []
    org_id = hsds_id(SOURCE, "organization", record["id"])
    out.append(("organization", clean({
        "id": org_id,
        "name": record["name"],
        "alternate_name": "; ".join(record.get("alternateNames") or []),
        "description": strip_html(record.get("description")),
        "email": record.get("email"),
        "website": record.get("url"),
        "tax_status": record.get("taxStatus"),
        "tax_id": record.get("taxId"),
        "year_incorporated": record.get("yearIncorporated"),
        "legal_status": record.get("legalStatus"),
        **source_tag(SOURCE, record["id"]),
    })))
    out += [("phone", _phone(p, organization_id=org_id)) for p in record.get("phones") or []]

    taxonomy_used = False
    for svc in record.get("services") or []:
        svc_id = hsds_id(SOURCE, "service", svc["id"])
        meta = svc.get("meta") or {}
        out.append(("service", clean({
            "id": svc_id,
            # Always the parent organization: 4 inactive stub services have idOrganization null.
            "organization_id": org_id,
            "name": svc["name"],
            "alternate_name": "; ".join(svc.get("alternateNames") or []),
            "description": strip_html(svc.get("description")),
            "url": svc.get("url"),
            "email": svc.get("email"),
            "status": meta.get("status"),
            "interpretation_services": svc.get("interpretationServices"),
            "application_process": strip_html(svc.get("applicationProcess")),
            "fees_description": strip_html((svc.get("fees") or {}).get("description")),
            "eligibility_description": strip_html((svc.get("eligibility") or {}).get("description")),
            "wait_time": svc.get("waitTime"),
            "assured_date": meta.get("lastVerified"),
            # NDP gives a date only; HSDS wants a date-time, so midnight UTC (documented in README).
            "last_modified": f"{meta['lastUpdated']}T00:00:00Z" if meta.get("lastUpdated") else None,
            **source_tag(SOURCE, svc["id"]),
        })))
        out += [("phone", _phone(p, service_id=svc_id)) for p in svc.get("phones") or []]
        out += _schedules(svc.get("schedules"), service_id=svc_id)
        out += _languages(svc.get("languages"), svc["id"], service_id=svc_id)
        for area in svc.get("serviceAreas") or []:
            out.append(("service_area", clean({
                "id": hsds_id(SOURCE, "service_area", area["id"]),
                "service_id": svc_id,
                "name": area.get("value"),
                "description": area.get("type"),  # "county" or "locality"
            })))
        terms, leaves = _taxonomy_terms(svc.get("taxonomy"))
        taxonomy_used = taxonomy_used or bool(terms)
        out += [("taxonomy_term", term) for term in terms]
        # Link the service to its most specific terms; broader levels follow from parent_id.
        out += [
            ("attribute", {
                "id": hsds_id(SOURCE, "attribute", f"{svc['id']}:{code}"),
                "link_id": svc_id,
                "link_entity": "service",
                "link_type": "taxonomy",
                "taxonomy_term_id": term_id,
            })
            for code, term_id in leaves
        ]

    for loc in record.get("locations") or []:
        loc_id = hsds_id(SOURCE, "location", loc["id"])
        out.append(("location", clean({
            "id": loc_id,
            "location_type": "physical",
            "organization_id": org_id,
            "name": loc.get("name"),
            "alternate_name": "; ".join(loc.get("alternateNames") or []),
            "description": strip_html(loc.get("description")),
            "transportation": loc.get("transportation"),
            "latitude": loc.get("latitude"),
            "longitude": loc.get("longitude"),
            "url": loc.get("url"),
            **source_tag(SOURCE, loc["id"]),
        })))
        for addr in loc.get("addresses") or []:
            # "Various Locations" addresses have no city or postal code; validation quarantines
            # just the address and the location itself is kept.
            out.append(("address", clean({
                "id": hsds_id(SOURCE, "address", addr["id"]),
                "location_id": loc_id,
                "address_1": addr.get("street"),
                "city": addr.get("city"),
                "region": addr.get("county"),
                "state_province": addr.get("state"),
                "postal_code": addr.get("postalCode"),
                "country": COUNTRIES.get(addr.get("country"), addr.get("country")),
                "address_type": ADDRESS_TYPES.get(addr.get("type"), addr.get("type")),
            })))
        access = (loc.get("accessibility") or {}).get("description")
        if access:
            out.append(("accessibility", {
                "id": hsds_id(SOURCE, "accessibility", loc["id"]),
                "location_id": loc_id,
                "description": access,
            }))
        out += [("phone", _phone(p, location_id=loc_id)) for p in loc.get("phones") or []]
        out += _schedules(loc.get("schedules"), location_id=loc_id)

    for sal in record.get("servicesAtLocations") or []:
        if not (sal.get("idService") and sal.get("idLocation")):
            continue  # 4 inactive links with no location: nothing to link (documented in README)
        sal_id = hsds_id(SOURCE, "service_at_location", sal["id"])
        out.append(("service_at_location", clean({
            "id": sal_id,
            "service_id": hsds_id(SOURCE, "service", sal["idService"]),
            "location_id": hsds_id(SOURCE, "location", sal["idLocation"]),
        })))
        out += [("phone", _phone(p, service_at_location_id=sal_id)) for p in sal.get("phones") or []]
        out += _schedules(sal.get("schedules"), service_at_location_id=sal_id)

    if taxonomy_used:
        out.insert(1, ("taxonomy", dict(TAXONOMY)))  # before the terms that point at it
    return out


def _phone(phone: dict, **parent) -> dict:
    number, extension = split_phone(phone.get("number"))
    return clean({
        "id": hsds_id(SOURCE, "phone", phone["id"]),
        "number": number,
        "extension": to_number(phone.get("extension")) or extension,
        "type": PHONE_TYPES.get(phone.get("type"), "voice"),
        "description": phone.get("description") or phone.get("name"),
        **parent,
    })


def _schedules(schedules: list[dict] | None, **parent) -> list[tuple[str, dict]]:
    """NDP open[] per day -> one HSDS weekly schedule per distinct (opens, closes), days grouped.

    Times stay local Pacific wall-clock with no UTC offset, the same rule as ODRS.
    """
    out = []
    for sched in schedules or []:
        groups: dict[tuple[str, str], list[str]] = {}
        for slot in sched.get("open") or []:
            day = DAYS.get((slot.get("day") or "").lower())
            if day and slot.get("opensAt") and slot.get("closesAt"):
                groups.setdefault((slot["opensAt"], slot["closesAt"]), []).append(day)
        common = {
            "valid_from": sched.get("validFrom"),
            "valid_to": sched.get("validTo"),
            "description": (sched.get("description") or "").strip().rstrip(";"),
            **parent,
        }
        for (opens, closes), days in groups.items():
            out.append(("schedule", clean({
                "id": hsds_id(SOURCE, "schedule", f"{sched['id']}:{opens}-{closes}"),
                "freq": "WEEKLY",
                "byday": ",".join(sorted(days, key=DAY_ORDER.index)),
                "opens_at": opens,
                "closes_at": closes,
                **common,
            })))
        if not groups and common["description"]:
            out.append(("schedule", clean({"id": hsds_id(SOURCE, "schedule", sched["id"]), **common})))
    return out


def _languages(languages: dict | None, native_svc_id: str, **parent) -> list[tuple[str, dict]]:
    """'English, Spanish' -> one HSDS language object per name."""
    names = [n.strip() for n in ((languages or {}).get("description") or "").split(",") if n.strip()]
    return [
        ("language", clean({
            "id": hsds_id(SOURCE, "language", f"{native_svc_id}:{name}"),
            "name": name,
            "code": LANGUAGE_CODES.get(name),
            **parent,
        }))
        for name in names
    ]


def _taxonomy_terms(entries: list[dict] | None) -> tuple[list[dict], list[tuple[str, str]]]:
    """NDP taxonomyTermLevel1..6 -> one taxonomy_term per level, each pointing at its parent.

    Returns (terms, leaves): every term needed, parents first, and the (code, term id) of
    each most-specific term the service is tagged with.

    Term ids come from the path ("Education/Educational Institutions/Schools/School Districts"),
    so a level shared by many services (e.g. "Education") is one term. Only the deepest level
    has a code in NDP; the levels above it get none rather than a guessed one.
    Target populations (targets[], e.g. YB-8000 "Older Adults") become flat terms.
    """
    terms: dict[str, dict] = {}
    leaves: list[tuple[str, str]] = []
    for entry in entries or []:
        levels = [entry.get(f"taxonomyTermLevel{i}") for i in range(1, 7)]
        levels = [lvl for lvl in levels if lvl]
        parent_id = None
        for depth, name in enumerate(levels, start=1):
            term_id = hsds_id(SOURCE, "taxonomy_term", "/".join(levels[:depth]))
            is_leaf = depth == len(levels)
            terms.setdefault(term_id, clean({
                "id": term_id,
                "code": entry.get("taxonomyCode") if is_leaf else None,
                "name": name,
                "description": " > ".join(levels[:depth]),
                "parent_id": parent_id,
                "taxonomy_id": TAXONOMY_ID,
            }))
            parent_id = term_id
        if levels:
            leaves.append((entry.get("taxonomyCode") or "/".join(levels), parent_id))
        for target in entry.get("targets") or []:
            term_id = hsds_id(SOURCE, "taxonomy_term", f"target:{target['code']}")
            terms.setdefault(term_id, {
                "id": term_id,
                "code": target["code"],
                "name": target["term"],
                "description": f"Target population: {target['term']}",
                "taxonomy_id": TAXONOMY_ID,
            })
            leaves.append((f"target:{target['code']}", term_id))
    return list(terms.values()), leaves
