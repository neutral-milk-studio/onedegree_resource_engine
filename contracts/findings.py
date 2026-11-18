"""Findings contract — the shape of an AI maintenance finding and its evidence.

Findings are operational data stored SEPARATELY from HSDS records. A proposal
with no supporting evidence snippet is never stored as a finding.

Spec: Requirements 1.1, 22.1, 23.1, 23.2, 24.1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class Category(str, Enum):  # noqa: UP042 - str+Enum is intentional for JSON serialization (py3.9+)
    """The one category assigned to a record per completed check (Req 22.1)."""

    VERIFIED = "verified"
    DISCREPANCY = "discrepancy"
    ACTIVELY_BAD = "actively_bad"  # dead site / closed org / expired cert — most critical
    CHECK_FAILED = "check_failed"  # could not access / check errored — never a silent skip
    MISSING_INFORMATION = "missing_information"


class ReviewState(str, Enum):  # noqa: UP042 - str+Enum is intentional for JSON serialization (py3.9+)
    """Where a finding's proposal sits in the human review flow."""

    PENDING = "pending"
    APPLIED = "applied"
    EDITED = "edited"
    DISMISSED = "dismissed"


@dataclass
class Evidence:
    """Supporting evidence for a finding.

    A finding whose evidence has no non-empty ``snippet`` is not stored (Req 23.2).
    ``snippet`` holds a quote from the page, or the failure when a site can't be read.
    """

    url: str
    snippet: str  # A quote from the page, or a failure such as "HTTP 404".
    model: str = ""  # The model that produced the quote. Empty for a fetch failure.


@dataclass
class Finding:
    """A comparison result for one stored record."""

    id: str
    service_id: str  # ref to the HSDS record checked
    field: str | None  # e.g. "eligibility_description". None for a whole-record result.
    current_value: str | None
    proposed_value: str | None
    category: Category
    confidence: float
    model_id: str  # Empty when no model ran, such as for a fetch failure.
    checked_at: str  # ISO 8601
    evidence: list[Evidence] = field(default_factory=list)
    review_state: ReviewState = ReviewState.PENDING

    def has_evidence(self) -> bool:
        """True iff at least one evidence entry has a non-empty snippet."""
        return any(e.snippet and e.snippet.strip() for e in self.evidence)


NEEDS_FIELD_AND_MODEL = {Category.DISCREPANCY, Category.MISSING_INFORMATION}


def finding_problems(finding: Finding) -> list[str]:
    """Return why a finding can't be stored. An empty list means it's acceptable."""
    problems = []
    if not finding.has_evidence():
        problems.append("evidence needs a non-empty snippet")
    if any(not e.url.strip() for e in finding.evidence):
        problems.append("evidence needs a url")
    if not 0 <= finding.confidence <= 1:
        problems.append("confidence must be between 0 and 1")
    if finding.category in NEEDS_FIELD_AND_MODEL:
        if not finding.field:
            problems.append(f"{finding.category.value} needs a field")
        if not finding.model_id:
            problems.append(f"{finding.category.value} needs a model_id")
    if finding.category is Category.DISCREPANCY and finding.proposed_value is None:
        problems.append("discrepancy needs a proposed_value")
    if finding.category is Category.VERIFIED and finding.proposed_value is not None:
        problems.append("verified can't have a proposed_value")
    return problems


class FindingRejected(ValueError):
    """Raised when a finding breaks the rules in :func:`finding_problems`."""


@runtime_checkable
class FindingsSink(Protocol):
    """What the detection pipeline writes to."""

    def save(self, finding: Finding) -> str:
        """Store a finding and return its id, or raise :class:`FindingRejected`."""
        ...
