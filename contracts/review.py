"""Review API contract: what the internal review tool sends and receives.

These models add field-level targeting, evidence types, versioning, and source clashes
for the review tool. They don't change the existing store or findings contracts. The
reviewer's identity comes from authentication, so no request carries it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from contracts.findings import Category, ReviewState


class SourceType(StrEnum):
    """Where a value or piece of evidence came from."""

    NDP = "ndp"
    ODRS = "odrs"
    SPREADSHEET = "spreadsheet"
    WEBSITE = "website"
    FACEBOOK = "facebook"
    DIRECTORY = "directory"
    SPECIALIST = "specialist"
    PHONE = "phone"


class FieldRef(BaseModel):
    """Points at one field by object id, because list positions change."""

    entity_type: str
    entity_id: UUID
    field: str
    op: Literal["set", "add", "remove"] = "set"


class ReviewEvidence(BaseModel):
    """Supporting evidence. A dead site has no quote, so it carries an HTTP signal."""

    kind: Literal["quote", "http_signal"]
    source_type: SourceType
    url: str
    fetched_at: datetime
    snippet: str | None = None
    http_status: int | None = None
    error: str | None = None

    @model_validator(mode="after")
    def check_payload(self) -> ReviewEvidence:
        if self.kind == "quote" and not (self.snippet and self.snippet.strip()):
            raise ValueError("quote evidence needs the supporting text")
        if self.kind == "http_signal" and self.http_status is None and not self.error:
            raise ValueError("http_signal evidence needs an HTTP status or an error")
        return self


class ReviewFinding(BaseModel):
    """One proposed change or problem, as shown to a reviewer."""

    id: UUID
    service_id: UUID
    ref: FieldRef
    category: Category
    current_value: Any | None = None
    proposed_value: Any | None = None
    evidence: list[ReviewEvidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    model_id: str | None = None
    created_at: datetime
    review_state: ReviewState = ReviewState.PENDING

    @model_validator(mode="after")
    def check_finding(self) -> ReviewFinding:
        if self.category is Category.VERIFIED:
            raise ValueError("verified results are stored as a CheckResult, not a finding")
        if any(item.kind == "quote" for item in self.evidence) and not self.model_id:
            raise ValueError("findings based on extracted text must record the model id")
        return self


class CheckResult(BaseModel):
    """The latest maintenance check of one service, including verified results."""

    service_id: UUID
    category: Category
    checked_at: datetime
    finding_ids: list[UUID] = []


class Clash(BaseModel):
    """A source update that disagrees with a change made in this system."""

    id: UUID
    service_id: UUID
    ref: FieldRef
    source: str
    base_value: Any | None
    source_value: Any | None
    current_value: Any | None
    status: Literal["open", "resolved"] = "open"
    detected_at: datetime


class StoredRecord(BaseModel):
    """A service plus the version used to detect conflicting edits."""

    service: dict[str, Any]
    version: int


class FieldChange(BaseModel):
    ref: FieldRef
    value: Any | None


class FindingDecision(BaseModel):
    expected_version: int
    edited_value: Any | None = None


class FindingDismissal(BaseModel):
    reason: str | None = None


class RecordPatch(BaseModel):
    expected_version: int
    changes: list[FieldChange] = Field(min_length=1)


class ClashResolution(BaseModel):
    expected_version: int
    choice: Literal["source", "current", "custom"]
    custom_value: Any | None = None


class RecordSummary(BaseModel):
    id: UUID
    name: str
    organization_name: str | None
    source: str
    status: str
    version: int
    open_findings: int
    last_check: CheckResult | None = None


class RecordDetail(BaseModel):
    record: dict[str, Any]
    version: int
    findings: list[ReviewFinding]
    clashes: list[Clash]
    last_check: CheckResult | None = None


class ListResponse[T](BaseModel):
    total_items: int
    contents: list[T]


class ValidationProblem(BaseModel):
    path: str
    message: str
