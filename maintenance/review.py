"""Human review of findings: list (harm order), apply, keep current. AI proposes, people apply.

Applying a finding, in one transaction:
1. checks the record version the reviewer saw (stale -> 409, nothing saved),
2. applies the value to the nested HSDS service and appends an HSDS ``metadata`` entry,
3. re-validates the served form against HSDS v3.2.3 (invalid -> 422 with paths, nothing saved),
4. saves the record (version + 1) and the override (so re-ingest keeps the fix),
5. marks the finding applied/edited and writes the audit log (who, field, old, new, when, source).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api.app.store_pg import PgHsdsStore
from ingest.transformer.assemble import served
from maintenance.apply import ApplyError, add_metadata, apply_change
from maintenance.comparator.compare import FIELD_LABELS
from maintenance.trust import Trust, trust_for
from shared.hsds_validation import find_issues
from shared.models import AuditEntry, Check, Finding, Service


class ReviewError(Exception):
    def __init__(self, status: int, message: str, problems: list[dict] | None = None) -> None:
        super().__init__(message)
        self.status, self.message, self.problems = status, message, problems or []


@dataclass
class Decision:
    finding: Finding
    service: Service
    audit: AuditEntry


def _fmt(value: Any) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return "; ".join(
                f"{v.get('byday')} {v.get('opens_at')}-{v.get('closes_at')}" for v in value
            )
        return ", ".join(map(str, value))
    return str(value)


def trust_by_service(session: Session, service_ids: list[str] | None = None) -> dict[str, Trust]:
    latest = select(Check.service_id, func.max(Check.checked_at).label("at")).group_by(
        Check.service_id
    )
    if service_ids is not None:
        latest = latest.where(Check.service_id.in_(service_ids))
    latest = latest.subquery()
    checks = session.execute(
        select(Check.service_id, Check.category, Check.checked_at).join(
            latest, (Check.service_id == latest.c.service_id) & (Check.checked_at == latest.c.at)
        )
    ).all()
    open_q = select(Finding.service_id, Finding.harm_rank).where(Finding.review_state == "pending")
    if service_ids is not None:
        open_q = open_q.where(Finding.service_id.in_(service_ids))
    ranks: dict[str, list[int]] = {}
    for sid, rank in session.execute(open_q):
        ranks.setdefault(sid, []).append(rank)
    out = {sid: trust_for(cat, at, ranks.get(sid, [])) for sid, cat, at in checks}
    for sid in service_ids or []:
        out.setdefault(sid, trust_for(None, None, []))
    return out


def _target_field(finding: Finding) -> str | None:
    if finding.category in {"actively_bad", "suspicious"}:
        return "status"
    if finding.field in FIELD_LABELS and finding.field not in {"website"}:
        return finding.field
    return None


def apply_finding(
    store: PgHsdsStore,
    finding_id: str,
    expected_version: int,
    actor: str,
    edited_value: Any = None,
    seconds_to_fix: float | None = None,
) -> Decision:
    with store.session() as session:
        finding = session.get(Finding, finding_id)
        if finding is None:
            raise ReviewError(404, f"No finding with id {finding_id}")
        if finding.review_state != "pending":
            raise ReviewError(409, f"This finding was already {finding.review_state}.")
        service = session.get(Service, finding.service_id)
        if service is None:
            raise ReviewError(404, "The record for this finding no longer exists.")
        if service.version != expected_version:
            raise ReviewError(
                409,
                "Someone changed this record since you opened it. Reload to see the latest "
                f"version (you had {expected_version}, it is now {service.version}).",
            )
        field = _target_field(finding)
        if field is None:
            raise ReviewError(422, "There's nothing to apply for this finding. Use Keep current.")
        value = finding.proposed_value if edited_value is None else edited_value
        if value is None:
            raise ReviewError(
                422, "This finding has no proposed value. Choose a value to apply.",
                [{"path": field, "message": "a value is required"}],
            )  # fmt: skip
        current = finding.current_value if field != "status" else service.document.get("status")
        try:
            doc = apply_change(service.document, field, value, current=current)
        except ApplyError as exc:
            raise ReviewError(422, str(exc), [{"path": field, "message": str(exc)}]) from exc
        doc = add_metadata(doc, field, current, value, actor)
        issues = find_issues(served(doc), "service")
        if issues:
            raise ReviewError(
                422,
                "The change didn't pass HSDS validation, so nothing was saved.",
                [{"path": i.path, "message": i.message} for i in issues],
            )

        now = datetime.now(UTC)
        store.replace_document(session, service, doc)
        service.overrides = {
            **(service.overrides or {}),
            field: {"value": value, "base": current, "at": now.isoformat(), "by": actor},
        }
        edited = edited_value is not None and edited_value != finding.proposed_value
        finding.review_state = "edited" if edited else "applied"
        finding.decided_at, finding.decided_by = now, actor
        finding.decision_value, finding.seconds_to_fix = value, seconds_to_fix
        label = FIELD_LABELS.get(field, field)
        audit = AuditEntry(
            actor=actor,
            action="apply_finding",
            change_source="specialist" if edited else "ai",
            entity_type="service",
            entity_id=service.id,
            field=field,
            old_value=current,
            new_value=value,
            at=now,
            summary=(
                f"{actor} updated {label.lower()} on “{service.name}”: "
                f"{_fmt(current)} → {_fmt(value)}"
                + (f" ({seconds_to_fix:.0f} s)" if seconds_to_fix else "")
            ),
        )
        session.add(audit)
        session.flush()
        return Decision(finding, service, audit)


def dismiss_finding(
    store: PgHsdsStore,
    finding_id: str,
    actor: str,
    reason: str | None = None,
    seconds_to_fix: float | None = None,
) -> Decision:
    """Keep the current value. The record isn't changed; the decision is audited."""
    with store.session() as session:
        finding = session.get(Finding, finding_id)
        if finding is None:
            raise ReviewError(404, f"No finding with id {finding_id}")
        if finding.review_state != "pending":
            raise ReviewError(409, f"This finding was already {finding.review_state}.")
        service = session.get(Service, finding.service_id)
        now = datetime.now(UTC)
        finding.review_state, finding.dismiss_reason = "dismissed", reason
        finding.decided_at, finding.decided_by = now, actor
        finding.seconds_to_fix = seconds_to_fix
        label = FIELD_LABELS.get(finding.field or "", finding.field or "record")
        audit = AuditEntry(
            actor=actor,
            action="dismiss_finding",
            change_source="specialist",
            entity_type="service",
            entity_id=finding.service_id,
            field=finding.field,
            old_value=finding.current_value,
            new_value=finding.current_value,
            at=now,
            summary=(
                f"{actor} kept the current {label.lower()} on “{service.name if service else '?'}”"
                + (f": {reason}" if reason else "")
            ),
        )
        session.add(audit)
        session.flush()
        return Decision(finding, service, audit)
