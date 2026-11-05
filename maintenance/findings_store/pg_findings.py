"""Findings store over the ``ops`` schema, separate from the HSDS store.

Every finding goes through ``contracts.findings.finding_problems`` before it is saved, the
same rules the in-memory store applies: evidence with a URL and a non-empty snippet, a
confidence in [0, 1], a field and model id for discrepancy/missing information.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from contracts.findings import Category, Evidence, FindingRejected, finding_problems
from contracts.findings import Finding as ContractFinding
from maintenance.comparator.compare import FindingDraft
from maintenance.priority import harm_rank
from shared.models import Finding, LlmCache


def _text(value) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else json.dumps(value)


def contract_view(draft: FindingDraft, service_id: str, checked_at: str) -> ContractFinding:
    return ContractFinding(
        id="pending",
        service_id=service_id,
        field=draft.field,
        current_value=_text(draft.current_value),
        proposed_value=_text(draft.proposed_value),
        category=Category(draft.category),
        confidence=draft.confidence,
        model_id=draft.model_id or "",
        checked_at=checked_at,
        evidence=[Evidence(draft.evidence_url, draft.evidence_quote or "", draft.model_id or "")],
    )


class PgFindingsStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save_draft(self, check_id: str, service_id: str, draft: FindingDraft) -> tuple[str, bool]:
        """Validate and store; returns (finding id, created). Identical pending findings
        (same service, category, field and proposed value) are refreshed, not duplicated."""
        now = datetime.now(UTC)
        problems = finding_problems(contract_view(draft, service_id, now.isoformat()))
        if problems:
            raise FindingRejected("; ".join(problems))
        pending = self.session.scalars(
            select(Finding).where(
                Finding.service_id == service_id,
                Finding.review_state == "pending",
                Finding.category == draft.category,
            )
        ).all()
        for row in pending:
            if row.field == draft.field and row.proposed_value == draft.proposed_value:
                row.last_seen_at, row.check_id = now, check_id
                row.evidence_quote, row.evidence_url = draft.evidence_quote, draft.evidence_url
                row.risk_signals, row.risk_score = draft.risk_signals, draft.risk_score
                return row.id, False
        row = Finding(
            service_id=service_id,
            check_id=check_id,
            category=draft.category,
            field=draft.field,
            entity_type=draft.entity_type,
            entity_id=draft.entity_id,
            entity_field=draft.entity_field,
            current_value=draft.current_value,
            proposed_value=draft.proposed_value,
            evidence_url=draft.evidence_url,
            evidence_quote=draft.evidence_quote,
            http_status=draft.http_status,
            error=draft.error,
            confidence=draft.confidence,
            model_id=draft.model_id,
            harm_rank=harm_rank(draft.category, draft.field),
            risk_signals=draft.risk_signals,
            risk_score=draft.risk_score,
            created_at=now,
            last_seen_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row.id, True


class PgCache:
    """Model-response cache (``ops.llm_cache``), so an unchanged page costs nothing."""

    def __init__(self, sessions) -> None:
        self.sessions = sessions  # a callable returning a context-managed Session

    def get(self, key: str) -> dict | None:
        with self.sessions() as session:
            row = session.get(LlmCache, key)
            return row.value if row else None

    def put(self, key: str, value: dict) -> None:
        with self.sessions() as session:
            if session.get(LlmCache, key) is None:
                session.add(LlmCache(key=key, model_id=value.get("model_id", ""), value=value))
