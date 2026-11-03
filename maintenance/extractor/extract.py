"""Bedrock Anthropic fact extractor — STUB (task 19).

Extracts the facts specialists verify (counties/area, eligibility, hours,
languages, accessibility, contacts) and records the model id. Mockable so the
worker runs without live Bedrock. Spec: Requirements 20.1, 20.2.
"""

from __future__ import annotations

from typing import Any


def extract_facts(content: str) -> dict[str, Any]:
    """Return extracted facts plus the model id used."""
    raise NotImplementedError("task 19: Bedrock Anthropic fact extraction")
