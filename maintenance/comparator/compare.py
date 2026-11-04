"""Comparator: compare extracted page facts with the stored record, categorize the check,
and build evidence-backed findings (spec tasks 20.1 and 20.3).

Facts are compared as structured values, never as prose: county names, age bounds, income
percentages and dollar amounts, phone digits, email addresses and language names. A value is
compared only when both sides state the same kind of value. Text that can't be turned into a
value produces no comparison, so a rephrased sentence never flags, and a fact the page doesn't
mention is never a discrepancy. The website is treated as more accurate than the record for
the MVP, so the page value is the proposal.

Every check ends with at least one finding, never a silent skip, and gets exactly one category
by precedence: check_failed > actively_bad > discrepancy > missing_information > verified. A
check emits one finding per field that differs, plus one whole-record finding for a failure, a
closure, or a verified result. Every finding passes ``contracts.findings.finding_problems``.

Spec: Requirements 21.1, 21.2, 21.3, 22.1 to 22.6, 23.1, 23.2.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from contracts.findings import Category, Evidence, Finding, finding_problems
from contracts.website_source import WebsiteResult
from maintenance.extractor.extract import squash, visible_text
from shared.uuid_util import hsds_id

# Precedence for the one category of a check: earlier wins.
PRECEDENCE = (
    Category.CHECK_FAILED,
    Category.ACTIVELY_BAD,
    Category.DISCREPANCY,
    Category.MISSING_INFORMATION,
    Category.VERIFIED,
)

# Confidence that a finding is right. Named so the evaluation harness can tune them.
CONFIDENCE_CHECK_FAILED = 1.0  # the check certainly did not complete
CONFIDENCE_SITE_GONE = 0.9  # HTTP 404/410, expired certificate, or a domain that doesn't exist
CONFIDENCE_REPEATED_FAILURE = 0.8  # the site failed to load twice in a row
CONFIDENCE_CONTRADICTION = 0.9  # the page states a different value
CONFIDENCE_ADDITION = 0.7  # the page states something the record doesn't
CONFIDENCE_PHONE_MISMATCH = 0.7  # the page shows other numbers: could be a fax or another line
CONFIDENCE_ADDITIONAL_CONTACT = 0.5  # an extra phone next to the stored one
CONFIDENCE_MISSING = 0.8  # the record lacks a fact the page states
CONFIDENCE_CLOSURE_TEXT = 0.7  # the page announces a closure: a lead, not a verdict
CONFIDENCE_VERIFIED = 0.9  # at least one value was compared and matched
CONFIDENCE_NOTHING_TO_COMPARE = 0.3  # no contradiction, but nothing was confirmed either

GONE_STATUSES = {404, 410}
_EXPIRED_CERTIFICATE = re.compile(
    r"certificate[^.;]*\bexpired|expired[^.;]*\bcertificate|cert(?:ificate)?_has_expired",
    re.IGNORECASE,
)
_DOMAIN_GONE = re.compile(
    r"nxdomain|name or service not known|no such host|domain (?:not found|does not exist)"
    r"|nodename nor servname",
    re.IGNORECASE,
)

STATUS_FOR_CLOSURE = {"closed": "defunct", "temporarily_closed": "temporarily closed"}
# Stored statuses that already say as much as, or more than, the page.
ALREADY_CLOSED = {
    "closed": {"defunct", "inactive"},
    "temporarily_closed": {"temporarily closed", "defunct", "inactive"},
}


@dataclass
class Comparison:
    """The outcome of checking one record against one page."""

    category: Category
    findings: list[Finding]
    dropped: list[dict[str, Any]] = field(default_factory=list)  # findings refused, with why


# --------------------------------------------------------------------------- #
# Values from text
# --------------------------------------------------------------------------- #

# A capitalized word. Only these abbreviations may end in a period, so a name never runs
# across the end of a sentence ("Open Monday or Tuesday. Alameda County").
_NAME_WORD = r"(?:St\.|Mt\.|Ft\.|[A-Z][\w'-]*)"
_NAME = rf"{_NAME_WORD}(?:\s+{_NAME_WORD})*"
_COUNTY_WORD = r"[Cc]ount(?:y|ies)"
_COUNTY_LIST = re.compile(
    rf"({_NAME}(?:(?:\s*,\s*(?:(?:and|or)\s+|&\s+)?|\s+(?:and|or|&)\s+){_NAME})*)"
    rf"\s+{_COUNTY_WORD}\b"
)
_COUNTY_OF = re.compile(rf"\b{_COUNTY_WORD}\s+of\s+({_NAME})")
_LIST_SPLIT = re.compile(r"\s*,\s*(?:(?:and|or)\s+|&\s+)?|\s+(?:and|or|&)\s+")
_PERCENT = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(?:%|percent\b)", re.IGNORECASE)
_DOLLARS = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
_EXTENSION = re.compile(r"\s*(?:x|ext\.?|extension)\s*[0-9]+\s*$", re.IGNORECASE)
# A sentence ends at . ! ? or ; but not after an abbreviation such as "St." in "St. Louis".
_SENTENCE_END = re.compile(
    r"(?<=[.!?;])(?<!\bSt\.)(?<!\bMt\.)(?<!\bFt\.)(?<!\bCo\.)(?<!\bDr\.)(?<!\bNo\.)\s+"
)
# Stored text that already talks about where people live or what they earn, in words that
# can't be parsed into a value. A page value is then not an "additional" fact.
_LOCATION_WORDS = re.compile(
    r"\b(?:count(?:y|ies)|residents?|resid(?:e|es|ing|ency)|live|lives|living|area|region"
    r"|city|cities|state|zip|neighbou?rhoods?|district|town)\b",
    re.IGNORECASE,
)
_INCOME_WORDS = re.compile(
    r"\b(?:income|incomes|poverty|ami|fpl|smi|earn|earns|earning|salary|wages?)\b|[$%]",
    re.IGNORECASE,
)
# Capitalized words that start a sentence but never a county name ("Serving Alameda County").
_NOT_A_NAME = {
    "serving",
    "serves",
    "serve",
    "open",
    "residents",
    "resident",
    "we",
    "our",
    "the",
    "in",
    "for",
    "all",
    "only",
    "from",
    "within",
    "living",
    "live",
    "who",
    "people",
    "and",
    "of",
    "to",
    "available",
    "services",
    "eligible",
    "anyone",
    "everyone",
    "families",
    "located",
    "low-income",
    "households",
    "household",
}


def county_name(text: str) -> str:
    """Normalize an area name: "San Mateo Counties" and "the County of San Mateo" -> "san mateo"."""
    name = squash(text).lower()
    name = re.sub(r"^(?:the\s+)?(?:city\s+and\s+)?count(?:y|ies)\s+of\s+", "", name)
    name = re.sub(r"\s+count(?:y|ies)$", "", name)
    words = name.strip(" .,").split()
    while words and words[0] in _NOT_A_NAME:
        words.pop(0)
    return " ".join(words)


def counties_in(text: str) -> set[str]:
    """Return the county names a text mentions, normalized. Only named counties count.

    Each sentence is read on its own, so a name can't join words from two sentences.
    """
    found: set[str] = set()
    for sentence in _sentences(text):
        found |= {
            county_name(part)
            for match in _COUNTY_LIST.finditer(sentence)
            for part in _LIST_SPLIT.split(match.group(1))
        }
        found |= {county_name(match.group(1)) for match in _COUNTY_OF.finditer(sentence)}
    return {name for name in found if name}


def same_county(first: str, second: str) -> bool:
    """Same county, allowing extra leading words on one side.

    "uninsured santa clara" (from "Uninsured Santa Clara County residents") is Santa Clara.
    """
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    return bool(shorter) and longer.endswith(" " + shorter)


def counties_missing(names: set[str], others: set[str]) -> set[str]:
    """The names in ``names`` that are not the same county as any name in ``others``."""
    return {name for name in names if not any(same_county(name, other) for other in others)}


def same_counties(first: set[str], second: set[str]) -> bool:
    return not counties_missing(first, second) and not counties_missing(second, first)


def income_limits(text: str) -> dict[str, set[str]]:
    """Return the income figures a sentence states, by kind: percentages and dollar amounts."""
    return {
        "percent": {f"{float(value):g}%" for value in _PERCENT.findall(text)},
        "dollars": {f"${float(value.replace(',', '')):g}" for value in _DOLLARS.findall(text)},
    }


def _has_income(limits: dict[str, set[str]]) -> bool:
    return any(limits.values())


def phone_digits(number: str) -> str:
    """The digits of a phone number without its extension, at most the last ten."""
    digits = "".join(ch for ch in _EXTENSION.sub("", number) if "0" <= ch <= "9")
    return digits[-10:]


def same_phone(first: str, second: str) -> bool:
    """Same number, allowing one side to leave out the area code (7-digit local numbers)."""
    a, b = phone_digits(first), phone_digits(second)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = sorted((a, b), key=len)
    return len(shorter) >= 7 and longer.endswith(shorter)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_END.split(squash(text)) if part.strip()]


def _int_age(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str) and value.strip().isdecimal():
        return int(value.strip())
    if isinstance(value, int | float) and float(value).is_integer():
        return int(value)
    return None


# --------------------------------------------------------------------------- #
# Facts from the stored record
# --------------------------------------------------------------------------- #


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _places(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The record itself plus everything that can carry phones, hours or accessibility."""
    links = _dicts(record.get("service_at_locations"))
    locations = [link["location"] for link in links if isinstance(link.get("location"), dict)]
    return [record, *links, *locations, *_dicts(record.get("contacts"))]


def facts_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Read the comparable facts out of a stored HSDS service record."""
    areas = _dicts(record.get("service_areas"))
    places = _places(record)
    phones = [
        _text(phone.get("number"))
        for place in places
        for phone in _dicts(place.get("phones"))
        if _text(phone.get("number"))
    ]
    languages = _dicts(record.get("languages"))
    eligibility = _text(record.get("eligibility_description"))
    return {
        "eligibility": eligibility,
        "eligibility_counties": counties_in(eligibility),
        "service_area_names": [_text(a.get("name")) for a in areas if _text(a.get("name"))],
        "service_area_counties": counties_in(
            " ; ".join(f"{_text(a.get('name'))} {_text(a.get('extent'))}" for a in areas)
        ),
        "income": income_limits(eligibility),
        "min_age": _int_age(record.get("minimum_age")),
        "max_age": _int_age(record.get("maximum_age")),
        "phones": list(dict.fromkeys(phones)),
        "email": _text(record.get("email")),
        "languages": [_text(lang.get("name")) for lang in languages if _text(lang.get("name"))],
        "has_languages": bool(languages),
        "has_hours": any(_dicts(place.get("schedules")) for place in places),
        "has_accessibility": any(_dicts(place.get("accessibility")) for place in places),
        "status": _text(record.get("status")),
    }


# --------------------------------------------------------------------------- #
# Fetch failures
# --------------------------------------------------------------------------- #


def site_loaded(site: WebsiteResult) -> bool:
    """True when the page was fetched successfully and has content."""
    status_ok = site.status is None or 200 <= site.status < 300
    return site.error is None and status_ok and bool(site.content)


def definitely_gone(site: WebsiteResult) -> bool:
    """HTTP 404/410, an expired certificate, or a domain that doesn't exist."""
    error = site.error or ""
    return bool(
        site.status in GONE_STATUSES
        or _EXPIRED_CERTIFICATE.search(error)
        or _DOMAIN_GONE.search(error)
    )


def classify_fetch_failure(site: WebsiteResult, *, unreachable_before: bool = False) -> Category:
    """Category for a page that did not load.

    The site is ``actively_bad`` when it is definitely gone. Anything else (timeout,
    unreachable, server error, a certificate problem other than expiry) only means this check
    failed. It becomes ``actively_bad`` once the site has also failed to load on the previous
    check, because one failed fetch doesn't prove it is gone.
    """
    if definitely_gone(site) or unreachable_before:
        return Category.ACTIVELY_BAD
    return Category.CHECK_FAILED


def describe_failure(site: WebsiteResult) -> str:
    """A short, human-readable description of a failed fetch, such as "HTTP 404: not found"."""
    parts = []
    if site.status is not None and not 200 <= site.status < 300:
        parts.append(f"HTTP {site.status}")
    if site.error and site.error.strip():
        parts.append(site.error.strip())
    return ": ".join(parts) or "the page had no content"


# --------------------------------------------------------------------------- #
# Building findings
# --------------------------------------------------------------------------- #


def finding_id(service_id: str, field_name: str | None, checked_at: str) -> str:
    """Deterministic id, so re-saving the same check doesn't create a duplicate."""
    return hsds_id("finding", f"{service_id}:{field_name or 'check'}:{checked_at}")


@dataclass
class _Draft:
    field: str | None
    current: str | None
    proposed: str | None
    category: Category
    confidence: float
    quotes: list[str]


@dataclass
class _Outcome:
    """What one comparison step found: findings to make, and quotes of values that matched."""

    drafts: list[_Draft] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)


def _require(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required to build an evidence-backed finding")
    return value


class _Builder:
    def __init__(self, service_id: str, url: str, checked_at: str, page: str) -> None:
        self.service_id = service_id
        self.url = url
        self.checked_at = checked_at
        self.page = squash(page)
        self.findings: list[Finding] = []
        self.dropped: list[dict[str, Any]] = []

    def add(self, draft: _Draft, *, model: str, quoted_from_page: bool) -> bool:
        quotes = list(dict.fromkeys(q.strip() for q in draft.quotes if q and q.strip()))
        if quoted_from_page:
            # A quote must appear in the fetched page. The extractor already checked; this
            # re-check means a finding can never carry text the page doesn't contain.
            quotes = [q for q in quotes if squash(q) in self.page]
        finding = Finding(
            id=finding_id(self.service_id, draft.field, self.checked_at),
            service_id=self.service_id,
            field=draft.field,
            current_value=draft.current,
            proposed_value=draft.proposed,
            category=draft.category,
            confidence=draft.confidence,
            model_id=model,
            checked_at=self.checked_at,
            evidence=[Evidence(url=self.url, snippet=q, model=model) for q in quotes],
        )
        problems = finding_problems(finding)
        if problems:
            self.dropped.append(
                {"field": draft.field, "category": draft.category.value, "problems": problems}
            )
            return False
        self.findings.append(finding)
        return True


def _fact(facts: dict[str, Any], key: str) -> tuple[Any, str] | None:
    entry = facts.get(key)
    if not isinstance(entry, dict) or entry.get("value") is None:
        return None
    return entry["value"], entry.get("quote") or ""


def _page_counties(facts: dict[str, Any]) -> tuple[set[str], list[str], list[str]]:
    """Counties the page names, the quotes that name them, and their display names.

    Only real county names count. "Oakland" or "the Bay Area" are not compared with counties.
    """
    counties: set[str] = set()
    quotes: list[str] = []
    display: list[str] = []
    area = _fact(facts, "area_served")
    if area:
        from_quote = counties_in(area[1])
        from_items = set()
        for item in area[0]:
            if re.search(rf"\b{_COUNTY_WORD}\b", item):
                from_items |= counties_in(item) or {county_name(item)}
        named = from_items or {county_name(item) for item in area[0]} & from_quote
        if named:
            counties |= named
            quotes.append(area[1])
            display += [item for item in area[0] if county_name(item) in named]
    residence = _fact(facts, "eligibility_residence")
    if residence:
        found = counties_in(residence[0]) | counties_in(residence[1])
        if found:
            counties |= found
            quotes.append(residence[1])
    return {name for name in counties if name}, quotes, display


def _splice(
    stored: str, replacements: list[tuple[Callable[[str], Any], str]], additions: list[str]
) -> str:
    """Put the page's sentences in place of the stored sentences they replace.

    Each replacement swaps the first stored sentence that states the same kind of fact and
    drops later ones; the rest of the stored text is kept. A page sentence with nothing to
    replace is added at the end.
    """
    original = _sentences(stored)
    new: list[str | None] = list(original)
    placed: dict[int, list[str]] = {}
    extra = list(additions)
    for states_fact, quote in replacements:
        hits = [index for index, sentence in enumerate(original) if states_fact(sentence)]
        if not hits:
            extra.append(quote)
            continue
        placed.setdefault(hits[0], []).append(quote)
        for index in hits[1:]:
            if index not in placed:
                new[index] = None
    for index, quotes in placed.items():
        new[index] = " ".join(dict.fromkeys(quotes))
    kept = [sentence for sentence in new if sentence]
    kept += [quote for quote in dict.fromkeys(extra) if quote not in kept]
    return " ".join(_ended(sentence) for sentence in kept)


def _ended(sentence: str) -> str:
    """A sentence with end punctuation, so joined sentences don't run together."""
    sentence = sentence.strip()
    return sentence if sentence[-1:] in ".!?;" else sentence + "."


def _compare_eligibility(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    """Residence counties and income limits, which live in ``eligibility_description``.

    Counties stored in ``service_areas`` are compared on that field, and counties in the
    eligibility text on that one, so a contradiction in either is reported.
    """
    out = _Outcome()
    page_counties, county_quotes, county_display = _page_counties(facts)
    county_quote = " ".join(dict.fromkeys(county_quotes))
    income = _fact(facts, "eligibility_income")
    page_income = (
        {
            kind: income_limits(income[0])[kind] | income_limits(income[1])[kind]
            for kind in ("percent", "dollars")
        }
        if income
        else {"percent": set(), "dollars": set()}
    )
    eligibility = stored["eligibility"]
    area_counties = stored["service_area_counties"]

    if page_counties and area_counties:
        if same_counties(page_counties, area_counties):
            out.matched += county_quotes
        else:
            removed = bool(counties_missing(area_counties, page_counties))
            out.drafts.append(
                _Draft(
                    "service_areas",
                    "; ".join(stored["service_area_names"]),
                    "; ".join(county_display) or ", ".join(sorted(page_counties)),
                    Category.DISCREPANCY,
                    CONFIDENCE_CONTRADICTION if removed else CONFIDENCE_ADDITION,
                    county_quotes,
                )
            )

    replacements: list[tuple[Callable[[str], Any], str]] = []
    additions: list[str] = []
    confidence = 0.0
    eligibility_counties = stored["eligibility_counties"]
    if page_counties and eligibility_counties:
        if same_counties(page_counties, eligibility_counties):
            out.matched += county_quotes
        else:
            replacements.append((counties_in, county_quote))
            removed = bool(counties_missing(eligibility_counties, page_counties))
            confidence = CONFIDENCE_CONTRADICTION if removed else CONFIDENCE_ADDITION
    elif (
        page_counties
        and eligibility
        and not area_counties
        and not _LOCATION_WORDS.search(eligibility)
    ):
        additions.append(county_quote)  # the page adds a residence rule the record doesn't have
        confidence = CONFIDENCE_ADDITION

    record_income = stored["income"]
    shared = [kind for kind in ("percent", "dollars") if page_income[kind] and record_income[kind]]
    if shared:
        if any(page_income[kind] != record_income[kind] for kind in shared):
            replacements.append((lambda s: _has_income(income_limits(s)), income[1]))
            confidence = max(confidence, CONFIDENCE_CONTRADICTION)
        else:
            out.matched.append(income[1])
    elif _has_income(page_income) and eligibility and not _INCOME_WORDS.search(eligibility):
        additions.append(income[1])
        confidence = max(confidence, CONFIDENCE_ADDITION)

    if eligibility:
        if replacements or additions:
            quotes = [quote for _states, quote in replacements] + additions
            out.drafts.append(
                _Draft(
                    "eligibility_description",
                    eligibility,
                    _splice(eligibility, replacements, additions),
                    Category.DISCREPANCY,
                    confidence,
                    [q for quote in quotes for q in _quote_parts(quote, county_quotes)],
                )
            )
        return out

    # No eligibility text at all: everything the page says about it is missing information,
    # except counties the record already holds in service_areas.
    residence = _fact(facts, "eligibility_residence")
    page_says = []
    if page_counties and counties_missing(page_counties, area_counties):
        page_says += county_quotes
    elif residence and not counties_in(residence[0]) and not counties_in(residence[1]):
        page_says.append(residence[1])  # a residence rule that names no county
    if _has_income(page_income):
        page_says.append(income[1])
    page_says = list(dict.fromkeys(q for q in page_says if q))
    if page_says:
        out.drafts.append(
            _Draft(
                "eligibility_description",
                None,
                " ".join(page_says),
                Category.MISSING_INFORMATION,
                CONFIDENCE_MISSING,
                page_says,
            )
        )
    return out


def _quote_parts(quote: str, county_quotes: list[str]) -> list[str]:
    """The joined county quote is evidence as its separate page quotes."""
    joined = " ".join(dict.fromkeys(county_quotes))
    return county_quotes if county_quotes and quote == joined else [quote]


def _compare_age(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    out = _Outcome()
    age = _fact(facts, "eligibility_age")
    if not age or not isinstance(age[0], dict):
        return out
    bounds, quote = age
    for key, field_name in (("min", "minimum_age"), ("max", "maximum_age")):
        page_value = bounds.get(key)
        if page_value is None:
            continue
        record_value = stored[f"{key}_age"]
        if record_value is None:
            out.drafts.append(
                _Draft(
                    field_name,
                    None,
                    str(page_value),
                    Category.MISSING_INFORMATION,
                    CONFIDENCE_MISSING,
                    [quote],
                )
            )
        elif record_value != page_value:
            out.drafts.append(
                _Draft(
                    field_name,
                    str(record_value),
                    str(page_value),
                    Category.DISCREPANCY,
                    CONFIDENCE_CONTRADICTION,
                    [quote],
                )
            )
        else:
            out.matched.append(quote)
    return out


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _compare_phones(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    out = _Outcome()
    phones = _fact(facts, "phones")
    values = _strings(phones[0]) if phones else []
    if not values:
        return out
    quote = phones[1]
    record = stored["phones"]
    current = ", ".join(record) or None
    if not record:
        out.drafts.append(
            _Draft(
                "phones",
                None,
                ", ".join(values),
                Category.MISSING_INFORMATION,
                CONFIDENCE_MISSING,
                [quote],
            )
        )
        return out
    shared = [v for v in values if any(same_phone(v, r) for r in record)]
    extra = [v for v in values if v not in shared]
    if not shared:
        # The page shows only other numbers. That may be a fax or a different line, so it
        # is not scored as a certain contradiction.
        out.drafts.append(
            _Draft(
                "phones",
                current,
                ", ".join(values),
                Category.DISCREPANCY,
                CONFIDENCE_PHONE_MISMATCH,
                [quote],
            )
        )
    elif extra:
        out.drafts.append(
            _Draft(
                "phones",
                current,
                ", ".join([*record, *extra]),
                Category.DISCREPANCY,
                CONFIDENCE_ADDITIONAL_CONTACT,
                [quote],
            )
        )
    else:
        out.matched.append(quote)
    return out


def _compare_email(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    out = _Outcome()
    emails = _fact(facts, "emails")
    values = _strings(emails[0]) if emails else []
    if not values:
        return out
    quote = emails[1]
    record = stored["email"].lower()
    if not record:
        out.drafts.append(
            _Draft(
                "email", None, values[0], Category.MISSING_INFORMATION, CONFIDENCE_MISSING, [quote]
            )
        )
    elif record in {value.lower() for value in values}:
        out.matched.append(quote)
    else:
        # One email field: a page showing only other addresses contradicts it.
        out.drafts.append(
            _Draft(
                "email",
                stored["email"],
                values[0],
                Category.DISCREPANCY,
                CONFIDENCE_CONTRADICTION,
                [quote],
            )
        )
    return out


def _compare_languages(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    out = _Outcome()
    languages = _fact(facts, "languages")
    values = _strings(languages[0]) if languages else []
    if not values:
        return out
    quote = languages[1]
    if not stored["has_languages"]:
        out.drafts.append(
            _Draft(
                "languages",
                None,
                ", ".join(values),
                Category.MISSING_INFORMATION,
                CONFIDENCE_MISSING,
                [quote],
            )
        )
        return out
    record = {name.lower() for name in stored["languages"]}
    if not record:
        return out  # languages are stored by code only, so names can't be compared
    added = [value for value in values if value.lower() not in record]
    if added:
        out.drafts.append(
            _Draft(
                "languages",
                ", ".join(stored["languages"]),
                ", ".join([*stored["languages"], *added]),
                Category.DISCREPANCY,
                CONFIDENCE_ADDITION,
                [quote],
            )
        )
    else:
        out.matched.append(quote)  # a language the page leaves out is not proof either way
    return out


def _compare_presence(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    """Facts checked only for presence: comparing their values is not built yet."""
    out = _Outcome()
    hours = _fact(facts, "hours")
    hour_values = _strings(hours[0]) if hours else []
    if hour_values and not stored["has_hours"]:
        out.drafts.append(
            _Draft(
                "schedules",
                None,
                "; ".join(hour_values),
                Category.MISSING_INFORMATION,
                CONFIDENCE_MISSING,
                [hours[1]],
            )
        )
    access = _fact(facts, "accessibility")
    access_values = _strings(access[0]) if access else []
    if access_values and not stored["has_accessibility"]:
        out.drafts.append(
            _Draft(
                "accessibility",
                None,
                "; ".join(access_values),
                Category.MISSING_INFORMATION,
                CONFIDENCE_MISSING,
                [access[1]],
            )
        )
    return out


def _compare_status(stored: dict[str, Any], facts: dict[str, Any]) -> _Outcome:
    out = _Outcome()
    status = _fact(facts, "operating_status")
    if not status or status[0] not in STATUS_FOR_CLOSURE:
        return out
    value, quote = status
    if stored["status"] in ALREADY_CLOSED[value]:
        return out  # the record already says so, or something stronger
    out.drafts.append(
        _Draft(
            "status",
            stored["status"] or None,
            STATUS_FOR_CLOSURE[value],
            Category.ACTIVELY_BAD,
            CONFIDENCE_CLOSURE_TEXT,
            [quote],
        )
    )
    return out


def _no_change(draft: _Draft) -> bool:
    return (
        draft.current is not None
        and draft.proposed is not None
        and squash(draft.current) == squash(draft.proposed)
    )


def categorize(findings: list[Finding]) -> Category:
    """The one category of a completed check: the highest-precedence finding category.

    A check always has at least one finding, so an empty list is a programming error.
    """
    if not findings:
        raise ValueError("a check must have at least one finding")
    present = {finding.category for finding in findings}
    return next(category for category in PRECEDENCE if category in present)


def failure(
    record: dict[str, Any],
    category: Category,
    reason: str,
    *,
    url: str,
    checked_at: str,
    confidence: float | None = None,
) -> Comparison:
    """A whole-record finding for a check that could not finish. Never a silent skip."""
    _require(url, "url")
    if confidence is None:
        confidence = (
            CONFIDENCE_CHECK_FAILED if category is Category.CHECK_FAILED else CONFIDENCE_SITE_GONE
        )
    reason = reason.strip() if isinstance(reason, str) and reason.strip() else "the check failed"
    builder = _Builder(record["id"], url, checked_at, "")
    draft = _Draft(None, None, None, category, confidence, [reason])
    if not builder.add(draft, model="", quoted_from_page=False):
        raise ValueError(f"could not build a failure finding: {builder.dropped}")
    return Comparison(categorize(builder.findings), builder.findings, builder.dropped)


def compare(
    record: dict[str, Any],
    facts: dict[str, Any],
    *,
    url: str,
    model_id: str,
    checked_at: str,
    page_text: str,
) -> Comparison:
    """Compare page facts with the record and return the findings and the check's category.

    ``facts`` is the ``facts`` part of the extractor's result and ``page_text`` the visible
    text of the page, which every quote is checked against.
    """
    _require(url, "url")
    _require(model_id, "model_id")
    _require(page_text, "page_text")
    stored = facts_from_record(record)
    builder = _Builder(record["id"], url, checked_at, page_text)
    outcomes = [
        _compare_status(stored, facts),
        _compare_eligibility(stored, facts),
        _compare_age(stored, facts),
        _compare_phones(stored, facts),
        _compare_email(stored, facts),
        _compare_languages(stored, facts),
        _compare_presence(stored, facts),
    ]
    for outcome in outcomes:
        for draft in outcome.drafts:
            if _no_change(draft):
                continue  # proposing the value the record already has is not a finding
            builder.add(draft, model=model_id, quoted_from_page=True)

    if builder.findings:
        return Comparison(categorize(builder.findings), builder.findings, builder.dropped)
    if builder.dropped:
        # A difference was found but its quote couldn't be confirmed. The record must not be
        # reported as matching, so the check is reported as failed instead.
        fields = ", ".join(sorted({str(item["field"]) for item in builder.dropped}))
        result = failure(
            record,
            Category.CHECK_FAILED,
            f"a difference was found but its quote is not in the page ({fields})",
            url=url,
            checked_at=checked_at,
        )
        return Comparison(result.category, result.findings, builder.dropped)

    matched = [q for outcome in outcomes for q in outcome.matched if q]
    quotes = [q for q in dict.fromkeys(matched) if squash(q) in builder.page]
    confidence = CONFIDENCE_VERIFIED
    if not quotes:
        confidence = CONFIDENCE_NOTHING_TO_COMPARE
        first_line = next(line.strip() for line in page_text.split("\n") if line.strip())
        quotes = [first_line[:200]]
    verified = _Draft(None, None, None, Category.VERIFIED, confidence, quotes)
    builder.add(verified, model=model_id, quoted_from_page=True)
    if not builder.findings:
        return failure(
            record,
            Category.CHECK_FAILED,
            "could not quote the page for a verified result",
            url=url,
            checked_at=checked_at,
        )
    return Comparison(categorize(builder.findings), builder.findings, builder.dropped)


def check_page(
    record: dict[str, Any],
    site: WebsiteResult,
    extraction: dict[str, Any] | None,
    *,
    url: str,
    checked_at: str,
    unreachable_before: bool = False,
) -> Comparison:
    """Check one record against one fetched page.

    ``extraction`` is the extractor's result, or None when the page did not load (the worker
    doesn't call the model then).
    """
    _require(url, "url")
    if not site_loaded(site):
        category = classify_fetch_failure(site, unreachable_before=unreachable_before)
        repeated = category is Category.ACTIVELY_BAD and not definitely_gone(site)
        reason = describe_failure(site)
        if repeated:
            reason += " (also failed on the previous check)"
        return failure(
            record,
            category,
            reason,
            url=url,
            checked_at=checked_at,
            confidence=CONFIDENCE_REPEATED_FAILURE if repeated else None,
        )
    page = visible_text(site.content or "")
    if not page.strip():
        return failure(
            record,
            Category.CHECK_FAILED,
            "the page had no readable text",
            url=url,
            checked_at=checked_at,
        )
    if extraction is None:
        raise ValueError("a page that loaded needs an extraction result")
    return compare(
        record,
        extraction["facts"],
        url=url,
        model_id=extraction["model_id"],
        checked_at=checked_at,
        page_text=page,
    )
