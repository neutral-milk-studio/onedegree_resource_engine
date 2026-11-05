"""Optional Bedrock grounding for suspicious-listing signals (maintenance/risk.py).

Adds extra, page-quoted reasons for a specialist to read -- it never changes the score:
only the deterministic rules decide whether a listing is flagged. Skipped silently when
``BEDROCK_MODEL_ID`` isn't set, or on any failure (a flaky model call must never block a
maintenance run). Cached by a hash of the page content, like the main extractor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from pydantic import BaseModel, Field

from maintenance.extractor.parse import quote_on_page

log = logging.getLogger(__name__)

PROMPT_VERSION = "2026-11-05.1"

SYSTEM_PROMPT = (
    "You are reviewing one nonprofit resource listing's website for signs it may be "
    "targeting people in crisis. Never conclude it is a scam or fraud -- only point out "
    "specific reasons a human reviewer should look closer, each grounded in an exact "
    "quote (25 words or fewer) copied verbatim from the page. If you find nothing beyond "
    "what's obvious, return an empty list. Reply with strict JSON only, no other text: "
    '{"reasons": [{"reason": "...", "quote": "..."}]}'
)


class GroundedReason(BaseModel):
    reason: str = Field(max_length=200)
    quote: str = Field(min_length=1, max_length=250)


class GroundedReasons(BaseModel):
    reasons: list[GroundedReason] = Field(default_factory=list, max_length=5)


def cache_key(model_id: str, page_text: str) -> str:
    digest = hashlib.sha256(page_text.encode()).hexdigest()
    return hashlib.sha256(f"{PROMPT_VERSION}|{model_id}|{digest}".encode()).hexdigest()


def _parse(raw: str) -> GroundedReasons:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return GroundedReasons.model_validate(json.loads(text.strip()))


def ground_reasons(page_text: str, cache: Any = None, client_factory=None) -> list[str]:
    """Extra reasons grounded in the page, or [] if Bedrock isn't configured, the page has
    nothing extra to add, or the call fails for any reason."""
    model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
    if not model_id or not page_text.strip():
        return []
    key = cache_key(model_id, page_text)
    if cache is not None and (hit := cache.get(key)) is not None:
        return hit.get("reasons", [])

    try:
        if client_factory is None:

            def client_factory():
                import boto3

                return boto3.client(
                    "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2")
                )

        client = client_factory()
        response = client.converse(
            modelId=model_id,
            system=[{"text": SYSTEM_PROMPT}],
            messages=[{"role": "user", "content": [{"text": page_text[:12000]}]}],
            inferenceConfig={"maxTokens": 500, "temperature": 0},
        )
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        raw = "".join(block.get("text", "") for block in blocks)
        parsed = _parse(raw)
    except Exception as exc:  # noqa: BLE001 - any failure here just means no extra reasons
        log.info("suspicious-listing Bedrock grounding skipped", extra={"error": str(exc)})
        return []

    reasons = [item.reason for item in parsed.reasons if quote_on_page(item.quote, page_text)]
    if cache is not None:
        cache.put(key, {"reasons": reasons})
    return reasons
