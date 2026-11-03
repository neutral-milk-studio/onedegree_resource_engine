"""Comparator + materiality + categorization — STUB (task 20).

Compares extracted facts to the stored record, flags factual differences only,
and assigns exactly one Category. Spec: Requirements 21, 22.
"""

from __future__ import annotations

from typing import Any

from contracts.findings import Category, Finding
from contracts.website_source import WebsiteResult


def categorize(record: dict[str, Any], facts: dict[str, Any], site: WebsiteResult) -> Category:
    """Assign exactly one category for a completed check."""
    raise NotImplementedError("task 20.3: assign one of the five categories")


def compare(record: dict[str, Any], facts: dict[str, Any]) -> list[Finding]:
    """Produce evidence-backed findings for factual (not wording) differences."""
    raise NotImplementedError("task 20.1: materiality comparison -> findings")
