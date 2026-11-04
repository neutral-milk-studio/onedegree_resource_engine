"""Cross-source conflicts: the same organization from two sources with different values.

Runs after every ingest. Organizations are matched across sources by normalized name or by
website domain. For each matched pair, these fields are compared after normalizing (so
formatting never counts as a conflict):

- ``phone``: no phone number in common (digits only)
- ``email``: no email address in common (case-insensitive)
- ``website``: different domains (scheme, ``www.`` and trailing slashes ignored)

A specialist picks the winning value; it is applied to every service of the organization in
the losing source(s), with HSDS metadata, an override (so re-ingest keeps it), and the audit
log. A conflict whose values come to match is closed automatically.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from urllib.parse import urlparse

from sqlalchemy import select

from api.app.store_pg import PgHsdsStore
from maintenance.extractor.parse import phone_digits
from shared.models import Conflict, Service

log = logging.getLogger(__name__)

FIELDS = ("phone", "email", "website")
_STOP = {"the", "of", "and", "inc", "incorporated", "llc", "a"}


def name_key(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name or "")
    words = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _STOP]
    return " ".join(words)


def domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "//" in url else f"//{url}").hostname or ""
    return host.lower().removeprefix("www.") or None


@dataclass
class OrgSide:
    source: str
    organization_id: str
    name: str
    services: list[str] = field(default_factory=list)
    phones: Counter = field(default_factory=Counter)  # digits -> count
    phone_text: dict[str, str] = field(default_factory=dict)  # digits -> as written
    emails: Counter = field(default_factory=Counter)
    websites: Counter = field(default_factory=Counter)

    def top(self, counter: Counter) -> str | None:
        return counter.most_common(1)[0][0] if counter else None


def _sides(store: PgHsdsStore) -> list[OrgSide]:
    sides: dict[tuple[str, str], OrgSide] = {}
    with store.session() as session:
        for row in session.scalars(select(Service).where(Service.organization_id.is_not(None))):
            doc = row.document
            org = doc.get("organization") or {}
            side = sides.setdefault(
                (row.source, row.organization_id),
                OrgSide(row.source, row.organization_id, org.get("name") or row.organization_name),
            )
            side.services.append(row.id)
            phones = list(doc.get("phones") or []) + list(org.get("phones") or [])
            for sal in doc.get("service_at_locations") or []:
                phones += (sal.get("location") or {}).get("phones") or []
            for phone in phones:
                if digits := phone_digits(phone.get("number", "")):
                    side.phones[digits] += 1
                    side.phone_text.setdefault(digits, phone["number"])
            for email in (doc.get("email"), org.get("email")):
                if email:
                    side.emails[email.strip().lower()] += 1
            if site := org.get("website"):
                side.websites[site.strip()] += 1
    return list(sides.values())


def _pairs(sides: list[OrgSide]):
    groups: dict[str, list[OrgSide]] = defaultdict(list)
    for side in sides:
        keys = {f"name:{name_key(side.name)}"}
        keys |= {f"domain:{d}" for site in side.websites if (d := domain(site))}
        for key in keys:
            groups[key].append(side)
    seen = set()
    for key, members in groups.items():
        for a, b in combinations(sorted(members, key=lambda s: (s.source, s.organization_id)), 2):
            if a.source == b.source or (a.organization_id, b.organization_id) in seen:
                continue
            seen.add((a.organization_id, b.organization_id))
            yield key, a, b


def _differences(a: OrgSide, b: OrgSide) -> dict[str, tuple]:
    out = {}
    if a.phones and b.phones and not set(a.phones) & set(b.phones):
        out["phone"] = (
            a.phone_text[a.top(a.phones)], [a.phone_text[d] for d in a.phones],
            b.phone_text[b.top(b.phones)], [b.phone_text[d] for d in b.phones],
        )  # fmt: skip
    if a.emails and b.emails and not set(a.emails) & set(b.emails):
        out["email"] = (a.top(a.emails), sorted(a.emails), b.top(b.emails), sorted(b.emails))
    da = {domain(s) for s in a.websites} - {None}
    db = {domain(s) for s in b.websites} - {None}
    if da and db and not da & db:
        out["website"] = (
            a.top(a.websites),
            sorted(a.websites),
            b.top(b.websites),
            sorted(b.websites),
        )
    return out


@dataclass
class DetectReport:
    pairs: int = 0
    opened: int = 0
    refreshed: int = 0
    closed: int = 0


def detect_conflicts(store: PgHsdsStore) -> DetectReport:
    report = DetectReport()
    now = datetime.now(UTC)
    found: set[tuple[str, str, str]] = set()
    with store.session() as session:
        for _key, a, b in _pairs(_sides(store)):
            report.pairs += 1
            match_key = f"{a.organization_id}|{b.organization_id}"
            for fld, (a_val, a_vals, b_val, b_vals) in _differences(a, b).items():
                found.add((match_key, fld, "open"))
                row = session.scalars(
                    select(Conflict).where(
                        Conflict.match_key == match_key, Conflict.field == fld,
                        Conflict.status == "open",
                    )
                ).first()  # fmt: skip
                if row:
                    row.a_value, row.a_values, row.b_value, row.b_values = (
                        a_val,
                        a_vals,
                        b_val,
                        b_vals,
                    )
                    row.last_seen_at = now
                    report.refreshed += 1
                    continue
                session.add(
                    Conflict(
                        match_key=match_key, organization_name=a.name, field=fld,
                        a_source=a.source, a_organization_id=a.organization_id,
                        a_value=a_val, a_values=a_vals,
                        b_source=b.source, b_organization_id=b.organization_id,
                        b_value=b_val, b_values=b_vals, detected_at=now, last_seen_at=now,
                    )
                )  # fmt: skip
                report.opened += 1
        for row in session.scalars(select(Conflict).where(Conflict.status == "open")):
            if (row.match_key, row.field, "open") not in found:
                row.status, row.resolution, row.resolved_at = "resolved", "now_match", now
                row.resolved_by = "system"
                report.closed += 1
    log.info("conflict detection", extra=report.__dict__)
    return report


class ConflictError(Exception):
    def __init__(self, status: int, message: str, problems: list[dict] | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.problems = status, message, problems or []


def resolve_conflict(
    store: PgHsdsStore, conflict_id: str, choice: str, actor: str, custom_value=None
) -> tuple[Conflict, int]:
    """Apply the winning value to the losing side(s). All services or none (one transaction).

    Returns the conflict and the number of service records updated.
    """
    from ingest.transformer.assemble import served
    from maintenance.apply import ApplyError, add_metadata, apply_change
    from shared.hsds_validation import find_issues
    from shared.models import AuditEntry

    with store.session() as session:
        row = session.get(Conflict, conflict_id)
        if row is None:
            raise ConflictError(404, f"No conflict with id {conflict_id}")
        if row.status != "open":
            raise ConflictError(409, "This conflict is already resolved.")
        if choice == "a":
            winner, losers = row.a_value, [(row.b_organization_id, row.b_value)]
        elif choice == "b":
            winner, losers = row.b_value, [(row.a_organization_id, row.a_value)]
        elif choice == "custom":
            if not isinstance(custom_value, str) or not custom_value.strip():
                raise ConflictError(422, "Enter the value to use.", [{"path": row.field,
                                    "message": "a value is required"}])  # fmt: skip
            winner = custom_value.strip()
            losers = [(row.a_organization_id, row.a_value), (row.b_organization_id, row.b_value)]
        else:
            raise ConflictError(422, "choice must be a, b or custom")

        now = datetime.now(UTC)
        updated = 0
        for org_id, old_value in losers:
            for service in session.scalars(
                select(Service).where(Service.organization_id == org_id)
            ):
                current = old_value
                try:
                    doc = apply_change(service.document, row.field, winner, current=current)
                except ApplyError as exc:
                    problem = [{"path": row.field, "message": str(exc)}]
                    raise ConflictError(422, str(exc), problem) from exc
                if doc == service.document:
                    continue
                doc = add_metadata(doc, row.field, current, winner, actor)
                issues = find_issues(served(doc), "service")
                if issues:
                    raise ConflictError(
                        422,
                        f"“{service.name}” wouldn't be valid HSDS with this value; "
                        "nothing was saved.",
                        [{"path": i.path, "message": i.message} for i in issues],
                    )
                store.replace_document(session, service, doc)
                service.overrides = {
                    **(service.overrides or {}),
                    row.field: {
                        "value": winner,
                        "base": current,
                        "at": now.isoformat(),
                        "by": actor,
                    },
                }
                updated += 1
                session.add(AuditEntry(
                    actor=actor, action="resolve_conflict", change_source="conflict",
                    entity_type="service", entity_id=service.id, field=row.field,
                    old_value=current, new_value=winner, at=now,
                    summary=(f"{actor} resolved the {row.field} conflict for "
                             f"{row.organization_name}: “{service.name}” now uses {winner}"),
                ))  # fmt: skip
        row.status, row.resolution, row.resolved_value = "resolved", choice, winner
        row.resolved_by, row.resolved_at = actor, now
        session.flush()
        return row, updated
