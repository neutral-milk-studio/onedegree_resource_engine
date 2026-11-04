"""A stand-in for the model, for local runs and demos with no AWS account.

It answers in the same format as Bedrock, with quotes taken from the page, so the extractor and
comparator run unchanged. It reads only plainly formatted facts. Don't use it to judge accuracy.
"""

from __future__ import annotations

import json
import re

from maintenance.comparator.compare import counties_in
from maintenance.extractor.extract import ModelReply

MODEL_ID = "offline-rules"
_PHONE = re.compile(r"\(?\b\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+\w")
_HOURS = re.compile(r"\b\d{1,2}(:\d{2})?\s*(AM|PM|am|pm)\b")
_CLOSED = re.compile(r"permanently closed|has closed|closed its doors", re.IGNORECASE)


def _first_line(lines: list[str], test) -> str | None:
    return next((line for line in lines if test(line)), None)


def offline_invoke(_system: str, user: str) -> ModelReply:
    """Answer from the page text between the boundary tags."""
    page = re.sub(r"^<[^>]+>\n|\n</[^>]+>$", "", user)
    lines = [line.strip() for line in page.splitlines() if len(line.strip()) >= 12]
    facts: dict[str, dict] = {}

    if line := _first_line(lines, _CLOSED.search):
        facts["operating_status"] = {"value": "closed", "quote": line}
    if line := _first_line(lines, _PHONE.search):
        facts["phones"] = {"value": _PHONE.findall(line) and _phones(line), "quote": line}
    if line := _first_line(lines, _EMAIL.search):
        facts["emails"] = {"value": _EMAIL.findall(line), "quote": line}
    if line := _first_line(lines, _HOURS.search):
        facts["hours"] = {"value": [line], "quote": line}
    if line := _first_line(lines, counties_in):
        facts["area_served"] = {"value": sorted(counties_in(line)), "quote": line}
    if line := _first_line(lines, lambda text: text.lower().startswith("languages")):
        names = re.split(r"\s*,\s*|\s+and\s+", line.split(":", 1)[-1].strip(" ."))
        facts["languages"] = {"value": [name for name in names if name], "quote": line}
    return ModelReply(json.dumps(facts), MODEL_ID)


def _phones(line: str) -> list[str]:
    return [match.group(0) for match in _PHONE.finditer(line)]
