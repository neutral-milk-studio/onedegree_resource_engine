"""Sample content generator (demo + load testing).

Clones and mutates the supplied ODRS and NDP samples into any volume of schema-faithful mock data, adds curated
demo scenarios, writes matching website content with seeded discrepancies/failures (plus a ground-truth file for
evaluating the AI maintenance layer, technical doc 7.4) and policy PDFs for the chat agent.

    python -m tools.sample_data_generator --out data/generated --odrs 150 --ndp 80 --seed 7
"""

from __future__ import annotations
import argparse
import copy
import hashlib
import json
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CITIES = [
    ("Oakland", "94609", 37.8286, -122.259, "Alameda"),
    ("Berkeley", "94704", 37.8716, -122.2727, "Alameda"),
    ("San Jose", "95112", 37.3541, -121.8942, "Santa Clara"),
    ("Redwood City", "94063", 37.4852, -122.2364, "San Mateo"),
    ("San Francisco", "94110", 37.7479, -122.4156, "San Francisco"),
    ("Richmond", "94804", 37.9358, -122.3477, "Contra Costa"),
    ("Fremont", "94538", 37.5485, -121.9886, "Alameda"),
    ("Santa Rosa", "95404", 38.4404, -122.7141, "Sonoma"),
    ("Vallejo", "94590", 38.1041, -122.2566, "Solano"),
    ("San Rafael", "94901", 37.9735, -122.5311, "Marin"),
]
EXTRA_COUNTIES = [
    "Napa",
    "Solano",
    "Sonoma",
    "Marin",
    "San Mateo",
    "Santa Clara",
    "Contra Costa",
    "Alameda",
    "San Francisco",
]
SUFFIX = [
    "North",
    "Central",
    "Eastside",
    "Westside",
    "Harbor",
    "Valley",
    "Lakeside",
    "Hillcrest",
    "Bayview",
    "Riverside",
]
SCENARIOS = [
    ("verified", 50),
    ("phone_changed", 10),
    ("counties_expanded", 8),
    ("hours_changed", 8),
    ("eligibility_changed", 6),
    ("languages_changed", 4),
    ("closed", 3),
    ("not_found", 3),
    ("expired_certificate", 3),
    ("dns_failure", 2),
    ("timeout", 3),
]
EXPECTED = {
    "verified": {"verified", "missing_information"},
    "phone_changed": {"discrepancy"},
    "counties_expanded": {"discrepancy", "missing_information"},
    "hours_changed": {"discrepancy"},
    "eligibility_changed": {"discrepancy"},
    "languages_changed": {"discrepancy", "missing_information"},
    "closed": {"actively_bad"},
    "not_found": {"actively_bad"},
    "expired_certificate": {"actively_bad"},
    "dns_failure": {"actively_bad"},
    "timeout": {"check_failed"},
}
DAYSF = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40]


def fake_phone(rng, area="510") -> str:
    return f"({area}) 555-01{rng.randint(0, 99):02d}"


def pick(rng, table):
    r, acc = rng.randint(1, sum(w for _, w in table)), 0
    for name, w in table:
        acc += w
        if r <= acc:
            return name


# --------------------------------------------------------------------------------------------- ODRS
def clone_odrs(samples: list[dict], n: int, rng) -> list[dict]:
    out = []
    for i in range(n):
        cycle, t = divmod(i, len(samples))
        o = copy.deepcopy(samples[t])
        city = CITIES[(cycle + t) % len(CITIES)]
        off = 1_000_000 * (cycle + 1)
        o["id"] += off
        org = o["organization"]
        org["id"] += off
        org["name"] = f"{org['name']} – {SUFFIX[cycle % len(SUFFIX)]}" if cycle else org["name"]
        org["slug"] = f"{org['slug']}-{cycle}" if cycle else org["slug"]
        org["website"] = (
            f"http://www.{slug(org['name'])[:32]}-{org['id']}.example.org"  # unique per org
        )
        for p in o.get("phones", []):
            p["id"] += off
            p["digits"] = fake_phone(rng, "510").replace("(", "").replace(") ", "-")
        for loc in o.get("locations", []):
            loc["id"] += off
            loc["city"], loc["zip_code"] = city[0], city[1]
            loc["lat"], loc["long"] = (
                round(city[2] + rng.uniform(-0.02, 0.02), 5),
                round(city[3] + rng.uniform(-0.02, 0.02), 5),
            )
            for p in loc.get("phones", []):
                p["id"] += off
                p["digits"] = fake_phone(rng).replace("(", "").replace(") ", "-")
        o["slug"] = f"{o['slug']}-{cycle}"
        o["all_slugs"] = [o["slug"]]
        o["attachments"] = []
        stamp = (datetime.now() - timedelta(days=rng.randint(1, 400))).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        o["last_verified_at"] = o["updated_at"] = stamp
        out.append(o)
    return out


# --------------------------------------------------------------------------------------------- NDP
def _remap(old: str | None, cycle: int, mapping: dict):
    if old is None:
        return None
    if old not in mapping:
        mapping[old] = (
            f"211demo-{cycle}-{old.split('-', 1)[1]}"
            if old.startswith("211bayarea-")
            else str(int(old) * 10 + cycle % 10)
            if old.isdigit()
            else f"{old}-{cycle}"
        )
    return mapping[old]


def clone_ndp(samples: list[dict], n: int, rng) -> list[dict]:
    out = []
    for i in range(n):
        cycle, t = divmod(i, len(samples))
        o = copy.deepcopy(samples[t])
        m: dict = {}
        city = CITIES[(cycle * 3 + t) % len(CITIES)]

        def walk(node):
            if isinstance(node, dict):
                for k, v in list(node.items()):
                    if k in (
                        "id",
                        "idOrganization",
                        "idProgram",
                        "idService",
                        "idLocation",
                        "idResource",
                    ) and isinstance(v, str):
                        node[k] = _remap(v, cycle + 1, m)
                    elif k in ("locationIds", "serviceIds") and isinstance(v, list):
                        node[k] = [_remap(x, cycle + 1, m) for x in v]
                    else:
                        walk(v)
            elif isinstance(node, list):
                for x in node:
                    walk(x)

        walk(o)
        o["name"] = f"{o['name']} – {SUFFIX[(cycle + t) % len(SUFFIX)]}" if cycle else o["name"]
        o["url"] = f"http://www.{slug(o['name'])[:32]}-{slug(o['id'])}.example.org"
        o["dataOwner"] = "211demo"
        for ph in o.get("phones", []):
            ph["number"] = fake_phone(rng, "650")
        for s in o.get("services", []):
            for ph in s.get("phones", []):
                ph["number"] = fake_phone(rng, "650")
            s["url"] = o["url"]
        for loc in o.get("locations", []):
            loc["latitude"], loc["longitude"] = (
                round(city[2] + rng.uniform(-0.02, 0.02), 5),
                round(city[3] + rng.uniform(-0.02, 0.02), 5),
            )
            for ph in loc.get("phones", []):
                ph["number"] = fake_phone(rng, "650")
            for a in loc.get("addresses", []):
                a["city"], a["postalCode"], a["county"], a["state"] = (
                    city[0],
                    city[1],
                    city[4],
                    "CA",
                )
                a.setdefault("geocode", None)
        today = date.today().isoformat()
        for node in [o["meta"]] + [s["meta"] for s in o.get("services", [])]:
            node["lastVerified"] = node["lastUpdated"] = (
                (date.today() - timedelta(days=rng.randint(1, 300))).isoformat()
                if rng.random() < 0.8
                else today
            )
        out.append(o)
    return out


def curated_ndp() -> list[dict]:
    """Hand-written scenario records so the demo questions always have an answer."""

    def org(
        i,
        name,
        svc_name,
        desc,
        taxo,
        elig,
        docs,
        phone,
        hours_days,
        county,
        city,
        website_scenario,
        counties=("Alameda",),
    ):
        oid, sid, lid = f"211demo-C{i}0", f"211demo-C{i}1", f"211demo-C{i}2"
        c = next(x for x in CITIES if x[0] == city)
        meta = {
            "idResource": oid,
            "tags": [],
            "access": "public",
            "status": "active",
            "reasonInactive": None,
            "lastUpdated": date.today().isoformat(),
            "lastVerified": (date.today() - timedelta(days=40)).isoformat(),
            "created": None,
            "temporaryMessage": None,
        }
        return {
            "id": oid,
            "name": name,
            "alternateNames": [],
            "description": f"<p>{desc}</p>",
            "url": f"http://www.{slug(name)}.example.org",
            "email": None,
            "yearIncorporated": 2011,
            "taxStatus": "501(c)(3)",
            "taxId": None,
            "legalStatus": "Nonprofit",
            "funding": None,
            "contacts": [],
            "phones": [
                {
                    "id": f"9{i}01",
                    "name": "Phone",
                    "type": "main",
                    "number": phone,
                    "extension": None,
                    "description": "Main",
                    "isMain": True,
                    "access": None,
                }
            ],
            "programs": [],
            "dataOwner": "211demo",
            "dataOwnerDisplayName": "211 Demo",
            "dataSteward": None,
            "dataStewardDisplayName": None,
            "locations": [
                {
                    "id": lid,
                    "idOrganization": oid,
                    "name": f"{name} main site",
                    "alternateNames": [],
                    "description": None,
                    "contacts": [],
                    "phones": [],
                    "schedules": [],
                    "longitude": c[3],
                    "latitude": c[2],
                    "addresses": [
                        {
                            "id": f"9{i}02",
                            "type": "physical",
                            "postalCode": c[1],
                            "street": f"{100 + i * 17} Main Street",
                            "city": city,
                            "county": c[4],
                            "state": "CA",
                            "country": "United States",
                            "geocode": None,
                            "description": None,
                            "access": "public",
                        }
                    ],
                    "accessibility": {"description": "Wheelchair accessible", "types": None},
                    "transportation": "Near bus lines",
                    "languages": {"description": None, "codes": []},
                    "url": None,
                    "email": None,
                    "serviceIds": [sid],
                    "meta": meta,
                }
            ],
            "services": [
                {
                    "id": sid,
                    "idProgram": None,
                    "idOrganization": oid,
                    "name": svc_name,
                    "alternateNames": [],
                    "description": f"<p>{desc}</p>",
                    "contacts": [],
                    "phones": [
                        {
                            "id": f"9{i}03",
                            "name": "Phone",
                            "type": "main",
                            "number": phone,
                            "extension": None,
                            "description": "Main Number",
                            "isMain": True,
                            "access": None,
                        }
                    ],
                    "schedules": [
                        {
                            "id": f"9{i}04",
                            "type": None,
                            "validFrom": None,
                            "validTo": None,
                            "open": [
                                {"day": d, "opensAt": "09:00", "closesAt": "16:00"}
                                for d in hours_days
                            ],
                            "description": "",
                        }
                    ],
                    "taxonomy": [
                        {
                            "id": f"9{i}05",
                            "taxonomyTerm": taxo[-1][0],
                            "taxonomyCode": taxo[-1][1],
                            **{
                                f"taxonomyTermLevel{k + 1}": (taxo[k][0] if k < len(taxo) else None)
                                for k in range(6)
                            },
                            "targets": [],
                        }
                    ],
                    "applicationProcess": "Call or walk in during open hours",
                    "interpretationServices": None,
                    "email": None,
                    "url": f"http://www.{slug(name)}.example.org",
                    "waitTime": None,
                    "fees": {"type": None, "description": "No fee"},
                    "accreditations": [],
                    "licenses": [],
                    "languages": {"description": "English, Spanish", "codes": []},
                    "funding": None,
                    "eligibility": {"description": elig, "types": []},
                    "serviceAreas": [
                        {
                            "id": f"9{i}06",
                            "type": "county",
                            "value": f"{k} County",
                            "geoJson": None,
                            "geoComponents": [
                                {
                                    "postalCode": None,
                                    "locality": None,
                                    "county": k,
                                    "state": "California",
                                    "country": "United States",
                                }
                            ],
                        }
                        for k in counties
                    ],
                    "documents": {"description": docs, "types": []},
                    "locationIds": [lid],
                    "meta": meta,
                }
            ],
            "servicesAtLocations": [
                {
                    "id": f"9{i}07",
                    "idOrganization": oid,
                    "idService": sid,
                    "idLocation": lid,
                    "contacts": [],
                    "phones": [],
                    "schedules": [],
                    "url": None,
                    "email": None,
                    "meta": {**meta, "lastVerified": None},
                }
            ],
            "meta": meta,
            "_scenario": website_scenario,
        }

    wk = ["mon", "tue", "wed", "thu", "fri"]
    return [
        org(
            1,
            "Bay Area Diaper Bank",
            "Free diapers and baby supplies",
            "Distributes free diapers, wipes and formula to families with young children.",
            [
                ("Basic Needs", "B"),
                ("Clothing/Personal Items", "BD"),
                ("Baby Supplies", "BD-1800.1250"),
            ],
            "Families with children under 3 living in Alameda County.",
            "Photo identification, proof of address",
            "(510) 555-0141",
            wk,
            "Alameda",
            "Oakland",
            "verified",
        ),
        org(
            2,
            "Tri-City Rent Relief",
            "Emergency rental assistance",
            "Helps renters who are behind on rent avoid eviction with one-time payments.",
            [
                ("Housing/Shelter", "B"),
                ("Rental Assistance", "BH-3800"),
                ("Rent Payment Assistance", "BH-3800.6500"),
            ],
            "Renters with income at or below 60% of area median income who received a past-due notice.",
            "Photo identification, lease, income information, past-due rent notice",
            "(510) 555-0152",
            wk,
            "Alameda",
            "Fremont",
            "phone_changed",
            ("Alameda",),
        ),
        org(
            3,
            "Harbor Community Pantry",
            "Free groceries",
            "A neighborhood food pantry serving anyone who needs groceries.",
            [("Basic Needs", "B"), ("Food", "BD-5000"), ("Food Pantries", "BD-5000.2000")],
            "Open to all. No documents needed.",
            "None",
            "(415) 555-0163",
            ["tue", "thu", "sat"],
            "San Francisco",
            "San Francisco",
            "counties_expanded",
            ("San Francisco",),
        ),
        org(
            4,
            "Eastside Legal Aid",
            "Free eviction defense",
            "Free legal help for tenants facing eviction.",
            [
                ("Legal Services", "F"),
                ("Tenant Rights", "FT-2000"),
                ("Eviction Defense", "FT-2000.2300"),
            ],
            "Tenants in Santa Clara County with an eviction notice.",
            "Eviction notice, lease",
            "(408) 555-0174",
            wk,
            "Santa Clara",
            "San Jose",
            "expired_certificate",
            ("Santa Clara",),
        ),
        org(
            5,
            "Cedar Youth Center",
            "After-school tutoring",
            "Free tutoring and mentoring for teens.",
            [
                ("Individual and Family Life", "P"),
                ("Youth Development", "PH-9000"),
                ("Tutoring", "PH-9000.8500"),
            ],
            "Youth ages 12 to 18.",
            "None",
            "(510) 555-0185",
            wk,
            "Alameda",
            "Berkeley",
            "closed",
        ),
        org(
            6,
            "Valley Family Health Clinic",
            "Low-cost primary care",
            "Primary care on a sliding fee scale for uninsured and underinsured families.",
            [
                ("Health Care", "L"),
                ("Primary Care", "LH-1800"),
                ("Community Clinics", "LH-1800.1900"),
            ],
            "Anyone; fees based on income.",
            "Photo identification, income information",
            "(408) 555-0196",
            wk,
            "Santa Clara",
            "San Jose",
            "verified",
        ),
    ]


# ------------------------------------------------------------------------------------ website text
def _fmt_time(t: str) -> str:
    h, m = map(int, t.split(":"))
    return f"{(h % 12) or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _hours_line(intervals: dict[str, tuple[str, str]]) -> str:
    groups: dict[tuple, list[str]] = {}
    for d, iv in intervals.items():
        groups.setdefault(iv, []).append(d)
    parts = []
    for iv, days in groups.items():
        idx = sorted(DAYSF.index(d) for d in days)
        label = (
            f"{DAYSF[idx[0]]}-{DAYSF[idx[-1]]}"
            if len(idx) > 1 and idx == list(range(idx[0], idx[-1] + 1))
            else ", ".join(DAYSF[i] for i in idx)
        )
        parts.append(f"{label} {_fmt_time(iv[0])} - {_fmt_time(iv[1])}")
    return "Hours: " + "; ".join(parts)


def page_text(
    name, about, phones, hours=None, counties=None, languages=None, eligibility=None, drift=None
) -> str:
    phones = list(phones)
    counties, languages, eligibility = list(counties or []), list(languages or []), eligibility
    if drift == "phone_changed":
        phones = ["(510) 555-0999"]
    if drift == "counties_expanded":
        counties = counties + [c for c in EXTRA_COUNTIES if c not in counties][:4]
    if drift == "hours_changed" and hours:
        hours = {d: ("10:00", "15:00") for d in hours}
    if drift == "languages_changed":
        languages = languages + ["Vietnamese"]
    if drift == "eligibility_changed" and eligibility:
        eligibility = (
            re.sub(r"\d+", lambda m: str(int(m.group()) + 15), eligibility)
            if re.search(r"\d", eligibility)
            else eligibility + " Must be a resident of the county."
        )
    lines = [name, f"About: {about}"]
    if phones:
        lines.append("Phone: " + " or ".join(phones))
    if hours:
        lines.append(_hours_line(hours))
    if counties:
        lines.append("Counties served: " + ", ".join(counties))
    if languages:
        lines.append("Languages: " + ", ".join(languages))
    if eligibility:
        lines.append("Eligibility: " + eligibility)
    if drift == "closed":
        lines.append(
            "Notice: This organization has permanently closed. Thank you for your years of support."
        )
    return "\n".join(lines)


def build_sites(odrs: list[dict], ndp: list[dict], rng) -> tuple[dict, dict]:
    sites, truth, by_org = {}, {}, {}
    for o in odrs:
        by_org.setdefault(("odrs", o["organization"]["id"]), []).append(o)
    for (kind, _), opps in by_org.items():
        org = opps[0]["organization"]
        url, scenario = org["website"], pick(rng, SCENARIOS)
        about = re.sub(r"\s+", " ", opps[0]["description"])[:200]
        single = len(opps) == 1
        o = opps[0]
        p = o["properties"]
        hours = {}
        loc = (
            (o.get("locations") or [{}])[0].get("schedule", {})
            if len(o.get("locations") or []) == 1
            else {}
        )
        for i, d in enumerate(
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        ):
            if loc.get(f"{d}_start") and loc.get(f"{d}_end"):
                hours[DAYSF[i]] = (loc[f"{d}_start"][:5], loc[f"{d}_end"][:5])
        if single and not hours and rng.random() < 0.5:
            hours = {
                d: ("09:00", "17:00") for d in DAYSF[:5]
            }  # the website has hours the record lacks -> missing_information
        counties = [
            k[len("service-county-california-") :].replace("-", " ").title()
            for k, v in p.items()
            if k.startswith("service-county-") and str(v) == "true"
        ]
        langs = [
            k[5:].title()
            for k, v in p.items()
            if k.startswith("lang-")
            and str(v) == "true"
            and k != "lang-all-languages-by-interpreter"
        ]
        digs = list(
            dict.fromkeys(
                x["digits"].replace("-", "") for opp in opps for x in opp.get("phones", [])
            )
        )
        phones = [f"({d[:3]}) {d[3:6]}-{d[6:]}" for d in digs]
        scenario = _applicable(
            scenario, hours if single else None, p.get("elig-description") if single else None
        )
        text = page_text(
            org["name"],
            about,
            phones,
            hours if single else None,
            counties if single else None,
            langs if single else None,
            p.get("elig-description") if single else None,
            None
            if scenario in ("not_found", "expired_certificate", "dns_failure", "timeout")
            else scenario,
        )
        sites[url] = _site(scenario, text)
        truth[url] = scenario
    for o in ndp:
        url, scenario = o["url"], o.get("_scenario") or pick(rng, SCENARIOS)
        svcs = o["services"]
        s = svcs[0]
        hours = {}
        for slot in (s["schedules"][0].get("open") or []) if s.get("schedules") else []:
            hours[DAYSF[["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(slot["day"])]] = (
                slot["opensAt"],
                slot["closesAt"],
            )
        single = len(svcs) == 1
        counties = [
            a["value"].replace(" County", "")
            for a in s.get("serviceAreas", [])
            if "County" in (a.get("value") or "") or a.get("type") == "county"
        ]
        langs = [
            x.strip()
            for x in re.split(r"[;,]", (s.get("languages") or {}).get("description") or "")
            if x.strip()
        ]
        phones = list(
            dict.fromkeys(
                [ph["number"] for sv in svcs for ph in (sv.get("phones") or [])]
                + [ph["number"] for ph in o.get("phones", [])]
            )
        )
        about = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", o["description"]))[:200]
        if len(o.get("locations") or []) != 1:
            hours = {}
        scenario = _applicable(
            scenario,
            hours if single else None,
            (s.get("eligibility") or {}).get("description") if single else None,
        )
        text = page_text(
            o["name"],
            about,
            phones,
            hours if single else None,
            counties if single else None,
            langs if single else None,
            (s.get("eligibility") or {}).get("description") if single else None,
            None
            if scenario in ("not_found", "expired_certificate", "dns_failure", "timeout")
            else scenario,
        )
        sites[url] = _site(scenario, text)
        truth[url] = scenario
    return sites, truth


def _applicable(scenario, hours, eligibility):
    """A drift that cannot be expressed for this record is recorded as 'verified' in the ground truth."""
    if scenario == "hours_changed" and not hours:
        return "verified"
    if scenario == "eligibility_changed" and not eligibility:
        return "verified"
    return scenario


def _site(scenario, text):
    return {
        "not_found": {"status": 404, "text": "Page not found"},
        "expired_certificate": {"error": "expired_certificate"},
        "dns_failure": {"error": "dns_failure"},
        "timeout": {"error": "timeout"},
    }.get(scenario, {"status": 200, "text": text})


# ------------------------------------------------------------------------------------------ policies
POLICIES = [
    (
        "Bay Area Diaper Bank",
        "Diaper Distribution Policy",
        [
            "Eligibility: Families with a child under 3 who live in Alameda County may receive diapers.",
            "Limits: One pack of 50 diapers per child per month. Formula is available while supplies last.",
            "What to bring: A photo ID for the parent or guardian and a piece of mail that shows your address. If you do not have an address, tell the front desk and we will help.",
            "Pickup: Monday to Friday, 9 AM to 4 PM. Appointments are not needed.",
        ],
    ),
    (
        "Tri-City Rent Relief",
        "Rental Assistance Eligibility Policy",
        [
            "Income limit: Household income must be at or below 60 percent of area median income.",
            "What to bring: Photo ID, your lease, proof of income for the last 30 days, and the past-due rent notice from your landlord.",
            "Amount: One-time help up to 3,000 dollars, paid directly to the landlord. Households can receive help once in 12 months.",
            "Timing: Applications are reviewed in about 10 business days. Emergency requests cannot be accommodated.",
        ],
    ),
    (
        "Harbor Community Pantry",
        "Pantry Intake Policy",
        [
            "Anyone may use the pantry. You do not need ID or proof of address.",
            "Visits: One visit per household each week. Bring your own bags if you can.",
            "Hours: Tuesday, Thursday and Saturday. Doors open 30 minutes early for seniors.",
        ],
    ),
    (
        "Valley Family Health Clinic",
        "Sliding Fee Policy",
        [
            "Fees: Visit fees depend on income and household size. Bring proof of income to qualify for a lower fee.",
            "Uninsured patients: We help you apply for Medi-Cal at your first visit.",
            "Cancellations: Please call 24 hours ahead if you cannot make an appointment.",
        ],
    ),
]


def write_policies(out: Path) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        pdf = True
    except Exception:  # noqa: BLE001
        pdf = False
    for org, title, paras in POLICIES:
        base = slug(f"{org}-{title}")
        if pdf:
            path = out / f"{base}.pdf"
            c = canvas.Canvas(str(path), pagesize=letter)
            y = 740
            c.setFont("Helvetica-Bold", 16)
            c.drawString(72, y, title)
            y -= 20
            c.setFont("Helvetica", 11)
            c.drawString(72, y, org)
            y -= 32
            for p in paras:
                for line in re.findall(r".{1,92}(?:\s|$)", p):
                    c.drawString(72, y, line.strip())
                    y -= 15
                y -= 8
            c.save()
        else:
            path = out / f"{base}.txt"
            path.write_text(f"{title}\n{org}\n\n" + "\n\n".join(paras))
        manifest.append(
            {
                "organization": org,
                "title": title,
                "file": path.name,
                "effective_date": date.today().replace(month=1, day=1).isoformat(),
            }
        )
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


# ------------------------------------------------------------------------------------------- entry
def generate(out: Path, n_odrs=150, n_ndp=80, seed=7, samples=ROOT / "data" / "samples") -> dict:
    rng = random.Random(seed)
    odrs_t = json.loads((samples / "ODRS" / "BayesExport_20250729.json").read_text())[
        "opportunities"
    ]
    ndp_t = [
        o
        for f in sorted(samples.glob("NDP/national-data-platform-sample-*.json"))
        for o in json.loads(f.read_text())["data"]
    ]
    odrs = clone_odrs(odrs_t, n_odrs, rng)
    ndp = curated_ndp() + clone_ndp(ndp_t, n_ndp, rng)
    sites, truth = build_sites(odrs, ndp, rng)
    for d in ("odrs", "ndp"):
        for f in (out / d).glob("*.json") if (out / d).exists() else []:
            f.unlink()
    (out / "odrs").mkdir(parents=True, exist_ok=True)
    (out / "ndp").mkdir(parents=True, exist_ok=True)
    per = 50
    for i in range(0, len(odrs), per):
        page = odrs[i : i + per]
        (out / "odrs" / f"page-{i // per + 1:03d}.json").write_text(
            json.dumps(
                {
                    "paging": {
                        "total_count": len(odrs),
                        "per_page": per,
                        "current_page": i // per + 1,
                        "total_pages": -(-len(odrs) // per),
                        "previous_page": None,
                        "next_page": None,
                    },
                    "detected_language": None,
                    "opportunities": page,
                },
                ensure_ascii=False,
            )
        )
    for i in range(0, len(ndp), 25):
        chunk = [{k: v for k, v in o.items() if k != "_scenario"} for o in ndp[i : i + 25]]
        (out / "ndp" / f"page-{i // 25 + 1:03d}.json").write_text(
            json.dumps(
                {
                    "input": {
                        "countAvailable": len(ndp),
                        "countReturned": len(chunk),
                        "lastUpdated": "1970-01-01",
                        "includeInactive": False,
                        "dataOwners": "211demo",
                        "skip": i,
                        "take": 25,
                    },
                    "data": chunk,
                },
                ensure_ascii=False,
            )
        )
    (out / "websites.json").write_text(json.dumps(sites, indent=1))
    (out / "websites_truth.json").write_text(
        json.dumps(
            {
                "expected_categories": {k: sorted(v) for k, v in EXPECTED.items()},
                "scenarios": truth,
            },
            indent=1,
        )
    )
    manifest = write_policies(out / "policies")
    return {
        "odrs_records": len(odrs),
        "ndp_records": len(ndp),
        "websites": len(sites),
        "policies": len(manifest),
        "scenarios": {s: list(truth.values()).count(s) for s, _ in SCENARIOS},
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "generated"))
    ap.add_argument("--odrs", type=int, default=150)
    ap.add_argument("--ndp", type=int, default=80)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    print(json.dumps(generate(Path(a.out), a.odrs, a.ndp, a.seed), indent=2))


if __name__ == "__main__":
    main()
