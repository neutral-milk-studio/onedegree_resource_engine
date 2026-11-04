"""Suggest which HSDS field each spreadsheet column holds, with a confidence and a reason.

With ``BEDROCK_MODEL_ID`` set, one Amazon Bedrock Converse call sees the headers and a few
sample rows and returns strict JSON (validated: every column, known targets, confidence in
[0, 1]). Without it, or if the reply is invalid, offline rules suggest a mapping from header
synonyms and value patterns. A person always confirms the mapping before anything is stored.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ingest.adapters.spreadsheet import SYNONYMS, TARGETS, target_for
from maintenance.extractor.parse import find_phone

log = logging.getLogger(__name__)
SAMPLE_ROWS = 5


@dataclass
class ColumnSuggestion:
    column: str
    target: str
    confidence: float
    reason: str
    samples: list[str] = field(default_factory=list)


@dataclass
class Suggestion:
    columns: list[ColumnSuggestion]
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- offline rules ----------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
_URL = re.compile(r"^(https?://)?([a-z0-9-]+\.)+[a-z]{2,}(/\S*)?$", re.I)
_ZIP = re.compile(r"^\d{5}(-\d{4})?$")
_STATE = re.compile(r"^[A-Z]{2}$")
_PACKED = re.compile(r"\w[\w ]{1,20}:\s*[^|]+\|")
_HOURS = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)\w*\b.*\d", re.I)


def _share(values: list[str], test) -> float:
    filled = [v for v in values if v]
    return sum(1 for v in filled if test(v)) / len(filled) if filled else 0.0


def _pattern_target(values: list[str]) -> tuple[str, float, str] | None:
    checks = [
        ("packed", lambda v: bool(_PACKED.search(v)), "values look like “Key: value | Key: value”"),
        ("email", lambda v: bool(_EMAIL.match(v)), "values look like email addresses"),
        ("phone", lambda v: find_phone(v) is not None, "values look like phone numbers"),
        ("postal_code", lambda v: bool(_ZIP.match(v)), "values look like ZIP codes"),
        ("state", lambda v: bool(_STATE.match(v)), "values look like state codes"),
        ("website", lambda v: bool(_URL.match(v)), "values look like web addresses"),
        ("hours", lambda v: bool(_HOURS.search(v)), "values look like opening hours"),
    ]
    for target, test, reason in checks:
        share = _share(values, test)
        if share >= 0.6:
            return target, share, reason
    return None


def offline_suggestion(headers: list[str], rows: list[dict[str, str]]) -> Suggestion:
    columns: list[ColumnSuggestion] = []
    used: set[str] = set()
    for header in headers:
        values = [row.get(header, "") for row in rows]
        samples = [v for v in values if v][:3]
        lowered = header.lower().strip()
        exact = next((t for t, words in SYNONYMS if lowered in words), None)
        partial = target_for(header)
        pattern = _pattern_target(values)
        if exact:
            target, confidence, reason = exact, 0.9, f"the header “{header}” names this field"
        elif partial:
            target, confidence, reason = partial, 0.75, f"the header “{header}” suggests this field"
        elif pattern:
            target, share, why = pattern
            target, confidence, reason = target, round(0.5 + 0.3 * share, 2), why
        else:
            target, confidence, reason = "ignore", 0.3, "no match; check this column"
        if pattern and target == pattern[0] and target != "ignore":
            confidence = min(0.98, confidence + 0.08)
            reason += f", and {pattern[2]}"
        elif pattern and target not in {"ignore", pattern[0], "packed"} and pattern[1] >= 0.8:
            confidence = min(confidence, 0.55)
            reason += f", but {pattern[2]}"
        if not any(values):
            target, confidence, reason = "ignore", 0.9, "the column is empty"
        if target in used and target not in {"ignore", "packed"}:
            confidence = min(confidence, 0.4)
            reason += "; another column already maps here"
        used.add(target)
        columns.append(ColumnSuggestion(header, target, round(confidence, 2), reason, samples))
    return Suggestion(columns=columns, model_id="offline-rules")


# --- Bedrock ------------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You map columns of a spreadsheet of social-service listings to fields. Return ONLY JSON: "
    '{"columns": [{"column": str, "target": str, "confidence": number 0-1, "reason": str}]} '
    "with exactly one entry per column, in order. Allowed targets: "
    + ", ".join(f"{k} ({v})" for k, v in TARGETS.items())
    + ". Use 'packed' for a column holding several fields like 'Hours: ... | Languages: ...'. "
    "Use 'ignore' when unsure; lower the confidence rather than guess."
)


class _Column(BaseModel):
    column: str
    target: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class _Reply(BaseModel):
    columns: list[_Column]


def bedrock_suggestion(
    headers, rows, client, model_id, price_in=None, price_out=None
) -> Suggestion:
    price_in = (
        price_in
        if price_in is not None
        else float(os.environ.get("BEDROCK_INPUT_USD_PER_MTOK", "3.0"))
    )
    price_out = (
        price_out
        if price_out is not None
        else float(os.environ.get("BEDROCK_OUTPUT_USD_PER_MTOK", "15.0"))
    )
    sample = [{h: row.get(h, "") for h in headers} for row in rows[:SAMPLE_ROWS]]
    prompt = json.dumps({"columns": headers, "sample_rows": sample}, ensure_ascii=False)
    response = client.converse(
        modelId=model_id,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 1500, "temperature": 0},
    )
    usage = response.get("usage", {})
    raw = "".join(b.get("text", "") for b in response["output"]["message"]["content"])
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    reply = _Reply.model_validate(json.loads(text))
    if [c.column for c in reply.columns] != headers:
        raise ValueError("the reply doesn't list every column in order")
    bad = [c.target for c in reply.columns if c.target not in TARGETS]
    if bad:
        raise ValueError(f"unknown targets {bad}")
    values = {h: [v for v in (r.get(h, "") for r in rows) if v][:3] for h in headers}
    tokens_in, tokens_out = usage.get("inputTokens", 0), usage.get("outputTokens", 0)
    return Suggestion(
        columns=[
            ColumnSuggestion(c.column, c.target, round(c.confidence, 2), c.reason, values[c.column])
            for c in reply.columns
        ],  # fmt: skip
        model_id=model_id,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cost_usd=(tokens_in * price_in + tokens_out * price_out) / 1_000_000,
    )


def suggest_mapping(
    headers: list[str], rows: list[dict[str, str]], client_factory=None
) -> Suggestion:
    model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        return offline_suggestion(headers, rows)
    try:
        if client_factory is None:
            import boto3

            def client_factory():
                return boto3.client(
                    "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2")
                )

        return bedrock_suggestion(headers, rows, client_factory(), model_id)
    except (ValidationError, ValueError, json.JSONDecodeError, KeyError) as exc:
        log.warning("bedrock mapping reply invalid; using offline rules", extra={"error": str(exc)})
        fallback = offline_suggestion(headers, rows)
        fallback.note = f"The AI suggestion wasn't usable ({exc}); these come from offline rules."
        return fallback
    except Exception as exc:  # noqa: BLE001 - Bedrock unreachable: still useful, and say so
        log.warning("bedrock mapping failed; using offline rules", extra={"error": str(exc)})
        fallback = offline_suggestion(headers, rows)
        fallback.note = (
            f"Amazon Bedrock wasn't reachable ({type(exc).__name__}); using offline rules."
        )
        return fallback
