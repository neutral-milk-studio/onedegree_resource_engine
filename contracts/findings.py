"""Findings contract — the shape of an AI maintenance finding and its evidence.

Findings are operational data stored SEPARATELY from HSDS records. A proposal
with no supporting evidence snippet is never stored as a finding.

Spec: Requirements 1.1, 22.1, 23.1, 23.2, 24.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    """The one category assigned to a record per completed check (Req 22.1)."""

    VERIFIED = "verified"
    DISCREPANCY = "discrepancy"
    ACTIVELY_BAD = "actively_bad"          # dead site / closed org / expired cert — most critical
    CHECK_FAILED = "check_failed"          # could not access / check errored — never a silent skip
    MISSING_INFORMATION = "missing_information"


class ReviewState(str, Enum):
    """Where a finding's proposal sits in the human review flow."""

    PENDING = "pending"
    APPLIED = "applied"
    EDITED = "edited"
    DISMISSED = "dismissed"


@dataclass
class Evidence:
    """Supporting evidence for a proposal.

    A proposal whose evidence has no non-empty ``snippet`` is not a finding:
    it is dropped or marked low-confidence (Req 23.2).
    """

    url: str
    snippet: str   # supporting text; MUST be non-empty for a stored finding
    model: str     # model identifier that produced the proposal


@dataclass
class Finding:
    """A comparison result for one stored record."""

    id: str
    service_id: str                     # ref to the HSDS record checked
    field: str                          # e.g. "eligibility_description"
    current_value: str | None
    proposed_value: str | None
    category: Category
    confidence: float
    model_id: str
    checked_at: str                     # ISO 8601
    evidence: list[Evidence] = field(default_factory=list)
    review_state: ReviewState = ReviewState.PENDING

    def has_evidence(self) -> bool:
        """True iff at least one evidence entry has a non-empty snippet."""
        return any(e.snippet and e.snippet.strip() for e in self.evidence)
