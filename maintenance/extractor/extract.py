"""Fact extraction from a website page: an offline rules extractor and an Amazon Bedrock one.

Both return the same :class:`PageFacts`. The extractor only reads the page; deciding what is
a real (factual) difference is done in code by ``maintenance/comparator``. Every fact carries
the exact quote it came from, and the comparator drops any fact whose quote is not on the page.

Bedrock: Converse API, model from ``BEDROCK_MODEL_ID``, one call per record covering all
fields (the batching), strict JSON validated with Pydantic, cached by content hash so an
unchanged page and record never cost a second call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError

from maintenance.extractor.parse import (
    area_name,
    find_phone,
    html_to_text,
    normalize,
    parse_hours_line,
    sections,
    split_list,
)

log = logging.getLogger(__name__)

PROMPT_VERSION = "2026-11-04.1"
OFFLINE_MODEL_ID = "offline-rules"


class HoursSlot(BaseModel):
    day: str = Field(pattern=r"^(MO|TU|WE|TH|FR|SA|SU)$")
    opens: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    closes: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class Quoted(BaseModel):
    quote: str = Field(min_length=1, description="Exact text copied from the page")


class PhoneFact(Quoted):
    number: str


class HoursFact(Quoted):
    slots: list[HoursSlot] = Field(min_length=1)


class ListFact(Quoted):
    values: list[str] = Field(min_length=1)


class TextFact(Quoted):
    text: str


class AccessibilityFact(Quoted):
    wheelchair_accessible: bool | None = None


class EligibilityFact(Quoted):
    text: str
    differs_from_record: bool = False
    difference: str | None = None


class ClosureFact(Quoted):
    closed: bool


class FeeFact(Quoted):
    """Payment required before the organization helps (a scam risk signal)."""

    required: bool
    amount: str | None = None


class PageFacts(BaseModel):
    """What the page states. Absent means the page doesn't say (never a guess)."""

    phone: PhoneFact | None = None
    hours: HoursFact | None = None
    languages: ListFact | None = None
    service_area: ListFact | None = None
    eligibility: EligibilityFact | None = None
    accessibility: AccessibilityFact | None = None
    closure: ClosureFact | None = None
    organization_name: TextFact | None = None  # the name the site uses for itself
    fee_before_help: FeeFact | None = None


@dataclass
class Extraction:
    facts: PageFacts
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cached: bool = False


class ExtractionError(RuntimeError):
    """The page couldn't be turned into facts (model error, invalid JSON). -> check_failed."""


class Cache(Protocol):
    def get(self, key: str) -> dict | None: ...

    def put(self, key: str, value: dict) -> None: ...


class MemoryCache:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.items.get(key)

    def put(self, key: str, value: dict) -> None:
        self.items[key] = value


def cache_key(model_id: str, page_text: str, record_facts: dict) -> str:
    material = json.dumps(
        [PROMPT_VERSION, model_id, normalize(page_text), record_facts], sort_keys=True, default=str
    )
    return hashlib.sha256(material.encode()).hexdigest()


# --- Accessibility wording -> one fact ---------------------------------------------------

_NOT_ACCESSIBLE = re.compile(
    r"\b(not|no|isn't|is not|without)\b[^.]{0,40}\b(accessible|wheelchair|ramp|elevator)", re.I
)
_ACCESSIBLE = re.compile(r"\b(accessible|wheelchair|ramp|elevator|mobility aids?)\b", re.I)


def accessibility_class(text: str | None) -> bool | None:
    """True / False / None (unknown) from free text, so wording differences don't matter."""
    if not text or re.search(r"\bnot applicable\b|\bn/?a\b", text, re.I):
        return None
    if _NOT_ACCESSIBLE.search(text):
        return False
    if _ACCESSIBLE.search(text):
        return True
    return None


# --- Scam-signal facts (offline) ---------------------------------------------------------

_MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
_PAY_WORDS = re.compile(r"\b(fee|pay|payment|deposit|charge|gift card|wire)\b", re.I)
_NO_FEE = re.compile(
    r"\b(no|free of|without( any)?|never (ask|charge))\b[^.]{0,20}\b(fee|charge|cost)", re.I
)


def find_upfront_fee(text: str) -> FeeFact | None:
    """A sentence asking for money with a payment word, e.g. 'Pay a $99 processing fee'."""
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        if _NO_FEE.search(sentence):
            continue
        money = _MONEY.search(sentence)
        if money and _PAY_WORDS.search(sentence) and re.search(r"\b(fee|pay)\b", sentence, re.I):
            return FeeFact(required=True, amount=money.group(0), quote=sentence.strip())
    return None


def page_organization_name(html: str) -> str | None:
    """The name a page uses for itself: the header's first line, else the <title> prefix."""
    from maintenance.extractor.parse import html_lines

    lines = html_lines(html)
    title = next((text for tag, text in lines if tag == "title"), None)
    body = [text for tag, text in lines if tag != "title"]
    if body and len(body[0]) <= 80:
        return body[0]
    if title:
        return re.split(r"\s+[–|-]\s+", title)[0].strip()
    return None


# --- Offline extractor ---------------------------------------------------------------------

_HEADINGS = {
    "phone": ("phone", "call", "contact"),
    "hours": ("hours", "open", "when"),
    "languages": ("language",),
    "service_area": ("service area", "where we serve", "areas served", "serving"),
    "eligibility": ("who can get help", "eligibility", "who we serve", "who qualifies"),
    "accessibility": ("accessib",),
}


def _section(found: dict[str, list[str]], key: str) -> list[str]:
    for heading, lines in found.items():
        if any(word in heading for word in _HEADINGS[key]):
            return lines
    return []


class OfflineExtractor:
    """Deterministic rules for pages laid out with headings (the sample pages).

    Real-world pages vary too much for rules; that is what the Bedrock extractor is for.
    Eligibility meaning is not judged offline: only the model compares meaning.
    """

    model_id = OFFLINE_MODEL_ID

    def extract(self, html: str, record_facts: dict) -> Extraction:
        found = sections(html)
        facts = PageFacts()

        for line in _section(found, "phone"):
            if number := find_phone(line):
                facts.phone = PhoneFact(number=number, quote=line)
                break

        hours_lines = [line for line in _section(found, "hours") if parse_hours_line(line)]
        if hours_lines:
            slots = [
                HoursSlot(day=d, opens=o, closes=c)
                for line in hours_lines
                for d, o, c in parse_hours_line(line)
            ]
            facts.hours = HoursFact(slots=slots, quote=" ".join(hours_lines))

        if lines := _section(found, "languages"):
            facts.languages = ListFact(values=split_list(lines[0]), quote=lines[0])

        if lines := _section(found, "service_area"):
            names = [area_name(part) for part in split_list(lines[0])]
            if names := [n for n in names if n]:
                facts.service_area = ListFact(values=names, quote=lines[0])

        if lines := _section(found, "accessibility"):
            facts.accessibility = AccessibilityFact(
                wheelchair_accessible=accessibility_class(lines[0]), quote=lines[0]
            )

        if lines := _section(found, "eligibility"):
            facts.eligibility = EligibilityFact(text=lines[0], quote=lines[0])

        text = html_to_text(html)
        if name := page_organization_name(html):
            facts.organization_name = TextFact(text=name, quote=name)
        if fee := find_upfront_fee(text):
            facts.fee_before_help = fee
        closure = re.search(
            r"[^.\n]*\b(permanently closed|has closed|no longer (operating|open)|"
            r"closed (its|our) doors)\b[^.\n]*\.?",
            text,
            re.I,
        )
        if closure:
            facts.closure = ClosureFact(closed=True, quote=closure.group(0).strip())
        return Extraction(facts=facts, model_id=self.model_id)


# --- Bedrock extractor --------------------------------------------------------------------

SYSTEM_PROMPT = """You read one web page of a social-services organization and report the \
facts it states. You never guess. If the page does not state a fact, omit that key.

Return ONLY a JSON object with any of these keys:
  "phone": {"number": str, "quote": str}
  "hours": {"slots": [{"day": "MO|TU|WE|TH|FR|SA|SU", "opens": "HH:MM", "closes": "HH:MM"}], \
"quote": str}   (24-hour local time)
  "languages": {"values": [str], "quote": str}
  "service_area": {"values": [str], "quote": str}   (place names, without the word County)
  "eligibility": {"text": str, "differs_from_record": bool, "difference": str|null, "quote": str}
  "accessibility": {"wheelchair_accessible": bool|null, "quote": str}
  "closure": {"closed": bool, "quote": str}
  "organization_name": {"text": str, "quote": str}   (the name the site uses for itself)
  "fee_before_help": {"required": bool, "amount": str|null, "quote": str}   (money asked \
before any help is given, such as an application or processing fee)

Rules:
- Every "quote" is copied EXACTLY from the page text, character for character.
- For eligibility, compare the MEANING with the record's eligibility given below. Set \
differs_from_record true only if who qualifies is factually different (place, age, income, \
documents). Rewording is not a difference.
- No markdown, no commentary: the response is parsed as JSON."""


class BedrockExtractor:
    """Amazon Bedrock Converse extractor. Construct with a boto3 ``bedrock-runtime`` client."""

    def __init__(
        self,
        client: Any,
        model_id: str,
        cache: Cache | None = None,
        input_price_per_mtok: float | None = None,
        output_price_per_mtok: float | None = None,
        max_page_chars: int = 12000,
    ) -> None:
        self.client = client
        self.model_id = model_id
        self.cache = cache or MemoryCache()
        self.input_price = input_price_per_mtok
        self.output_price = output_price_per_mtok
        if self.input_price is None:
            self.input_price = float(os.environ.get("BEDROCK_INPUT_USD_PER_MTOK", "3.0"))
        if self.output_price is None:
            self.output_price = float(os.environ.get("BEDROCK_OUTPUT_USD_PER_MTOK", "15.0"))
        self.max_page_chars = max_page_chars

    def _prompt(self, page_text: str, record_facts: dict) -> str:
        record = {"eligibility": record_facts.get("eligibility")}
        return (
            f"Record eligibility (for comparison): {json.dumps(record)}\n\n"
            f"PAGE TEXT:\n<<<\n{page_text[: self.max_page_chars]}\n>>>"
        )

    def extract(self, html: str, record_facts: dict) -> Extraction:
        page_text = html_to_text(html)
        key = cache_key(self.model_id, page_text, {"eligibility": record_facts.get("eligibility")})
        if (hit := self.cache.get(key)) is not None:
            return Extraction(
                facts=PageFacts.model_validate(hit["facts"]), model_id=self.model_id, cached=True
            )
        try:
            response = self.client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[
                    {"role": "user", "content": [{"text": self._prompt(page_text, record_facts)}]}
                ],
                inferenceConfig={"maxTokens": 1200, "temperature": 0},
            )
        except Exception as exc:  # noqa: BLE001 - any Bedrock failure is a failed check
            raise ExtractionError(f"Bedrock call failed: {exc}") from exc

        usage = response.get("usage", {})
        tokens_in, tokens_out = usage.get("inputTokens", 0), usage.get("outputTokens", 0)
        cost = (tokens_in * self.input_price + tokens_out * self.output_price) / 1_000_000
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        raw = "".join(block.get("text", "") for block in blocks)
        log.info(
            "bedrock extraction",
            extra={"model_id": self.model_id, "input_tokens": tokens_in,
                   "output_tokens": tokens_out, "cost_usd": cost, "response": raw[:2000]},
        )  # fmt: skip
        facts = parse_model_json(raw)
        self.cache.put(key, {"facts": facts.model_dump(mode="json")})
        return Extraction(
            facts=facts,
            model_id=self.model_id,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=cost,
        )


def parse_model_json(raw: str) -> PageFacts:
    """Strict: the reply must be one JSON object matching PageFacts (a ```json fence is OK)."""
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    try:
        return PageFacts.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ExtractionError(f"model reply is not valid PageFacts JSON: {exc}") from exc


Extractor = OfflineExtractor | BedrockExtractor


def get_extractor(cache: Cache | None = None, client_factory: Callable | None = None) -> Extractor:
    """Bedrock when BEDROCK_MODEL_ID is set, otherwise the offline rules extractor."""
    model_id = os.environ.get("BEDROCK_MODEL_ID", "").strip()
    if not model_id:
        return OfflineExtractor()
    if client_factory is None:
        import boto3

        def client_factory():
            return boto3.client(
                "bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2")
            )

    return BedrockExtractor(client_factory(), model_id, cache=cache)
