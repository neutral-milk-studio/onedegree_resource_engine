"""Community submissions -> HSDS mapper (fictional demo source).

Listings submitted by the public through a web form: the channel where scam listings most
often arrive. The sample file is entirely fictional. Adding this source took this module
plus one entry in config/sources.yaml; the pipeline did not change.

Record shape::

    {"id": "c-1001", "submitted_at": "2026-10-28",
     "organization": {"id": "o-1", "name": "...", "website": "...", "description": "..."},
     "service": {"name": "...", "description": "...", "phone": "...", "email": "...",
                 "eligibility": "...", "service_area": ["..."]}}
"""

from ingest.transformer.hsds import clean, hsds_id, source_tag

SOURCE = "community"


def records(page: dict) -> list[dict]:
    return page.get("submissions") or []


def native_id(record: dict) -> str:
    return str(record["id"])


def map(record: dict) -> list[tuple[str, dict]]:
    org = record["organization"]
    svc = record["service"]
    org_id = hsds_id(SOURCE, "organization", org["id"])
    svc_id = hsds_id(SOURCE, "service", record["id"])
    out: list[tuple[str, dict]] = [
        ("organization", clean({
            "id": org_id,
            "name": org["name"],
            "description": org.get("description") or f"{org['name']}. Description not available.",
            "website": org.get("website"),
            **source_tag(SOURCE, org["id"]),
        })),
        ("service", clean({
            "id": svc_id,
            "organization_id": org_id,
            "name": svc["name"],
            "description": svc.get("description"),
            "status": "active",
            "email": svc.get("email"),
            "eligibility_description": svc.get("eligibility"),
            "last_modified": (
                f"{record['submitted_at']}T00:00:00Z" if record.get("submitted_at") else None
            ),
            **source_tag(SOURCE, record["id"], submitted_at=record.get("submitted_at")),
        })),
    ]  # fmt: skip
    if svc.get("phone"):
        out.append(("phone", {"id": hsds_id(SOURCE, "phone", record["id"]),
                              "service_id": svc_id, "number": svc["phone"]}))  # fmt: skip
    for name in svc.get("service_area") or []:
        area_id = hsds_id(SOURCE, "service_area", f"{record['id']}:{name}")
        out.append(("service_area", {"id": area_id, "service_id": svc_id, "name": name}))
    return out
