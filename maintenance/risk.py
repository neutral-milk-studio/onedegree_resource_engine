"""Suspicious-listing detection: deterministic rules, checked inside the same website check
(no extra model call needed for this part -- see ``maintenance/suspicious_bedrock.py`` for
the optional grounding step).

Each signal has a stable id, a weight (``maintenance/suspicious_settings.py``), a
plain-language reason, and -- where it comes from the page -- the exact quote it was found
in (never a full page, never more than needed to show the reason). Signals sum to a score
0-100; a listing is flagged ("suspicious") at ``THRESHOLD`` or above. One weak signal alone
(e.g. just "not in the registry") is never enough.

Wording: always "flagged for review" or "suspicious". Never "scam" or "fraud" as a
conclusion -- these are signals a specialist reviews, not a verdict. Never a person's name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

from maintenance.comparator.compare import FindingDraft
from maintenance.extractor.extract import find_upfront_fee
from maintenance.extractor.parse import quote_on_page
from maintenance.suspicious_settings import (
    NEW_DOMAIN_DAYS,
    THRESHOLD,
    WEIGHT_DOMAIN_MISMATCH,
    WEIGHT_FAKE_OFFICIAL,
    WEIGHT_FEE_BEFORE_HELP,
    WEIGHT_FEE_MISMATCH,
    WEIGHT_MESSAGING_ONLY_CONTACT,
    WEIGHT_NEW_DOMAIN,
    WEIGHT_NOT_IN_REGISTRY,
    WEIGHT_SENSITIVE_DATA_REQUEST,
    WEIGHT_URGENCY_PRESSURE,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "data" / "registry" / "known_orgs.json"
_STOP = {"the", "of", "and", "inc", "incorporated", "llc", "co", "center", "centre", "for", "a"}

URL_SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly"}
FREE_HOSTS = {
    "wixsite.com", "weebly.com", "blogspot.com", "sites.google.com", "github.io",
    "000webhostapp.com", "wordpress.com", "carrd.co", "square.site", "godaddysites.com",
}  # fmt: skip
FREE_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "aol.com",
    "icloud.com",
}

_SENTENCES = re.compile(r"(?<=[.!?])\s+|\n")


@dataclass(frozen=True)
class RiskSignal:
    code: str
    weight: int
    message: str
    quote: str | None = None


def _tokens(name: str) -> set[str]:
    name = re.sub(r"\([^)]*\)", " ", name or "")
    return {w for w in re.findall(r"[a-z0-9]+", name.lower()) if w not in _STOP}


def names_match(a: str, b: str) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return True  # nothing to compare is not a mismatch
    return len(ta & tb) / len(ta | tb) >= 0.5 or ta <= tb or tb <= ta


@cache
def _registry(path: str) -> frozenset[frozenset[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return frozenset(frozenset(_tokens(org["name"])) for org in data.get("organizations", []))


def in_registry(org_name: str) -> bool:
    names = _registry(os.environ.get("RE_SUSPICIOUS_REGISTRY_FILE", str(REGISTRY)))
    return frozenset(_tokens(org_name)) in names


def _quote(sentence: str, max_words: int = 25) -> str:
    words = sentence.strip().split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


def _first_match(pattern: re.Pattern, negation: re.Pattern | None, text: str) -> str | None:
    """The first sentence matching ``pattern`` that isn't also caught by ``negation``."""
    for sentence in _SENTENCES.split(text):
        if pattern.search(sentence) and not (negation and negation.search(sentence)):
            return sentence
    return None


# --- strong signals --------------------------------------------------------------------------

_SSN = re.compile(r"\b(ssn|social security number)\b|\b\d{3}-\d{2}-\d{4}\b", re.I)
_SENSITIVE = re.compile(
    r"\b(bank (login|account number)|routing number|card number|cvv|online banking password|"
    r"your password)\b",
    re.I,
)
_NO_SENSITIVE = re.compile(
    r"\b(never|won'?t|will not|don'?t) (ask|need|request)\b[^.]{0,30}"
    r"\b(ssn|social security|bank|password|card number)\b",
    re.I,
)


def _sensitive_data_signal(page_text: str) -> RiskSignal | None:
    sentence = _first_match(_SSN, _NO_SENSITIVE, page_text) or _first_match(
        _SENSITIVE, _NO_SENSITIVE, page_text
    )
    if not sentence:
        return None
    return RiskSignal(
        "sensitive_data_request",
        WEIGHT_SENSITIVE_DATA_REQUEST,
        "Asks for a Social Security number, bank, or card details up front",
        _quote(sentence),
    )


# --- medium signals --------------------------------------------------------------------------

_URGENCY = re.compile(
    r"\b(act now|limited spots?|guaranteed approval|guaranteed|apply (immediately|now))\b", re.I
)


def _urgency_signal(page_text: str) -> RiskSignal | None:
    sentence = _first_match(_URGENCY, None, page_text)
    if not sentence:
        return None
    return RiskSignal(
        "urgency_pressure", WEIGHT_URGENCY_PRESSURE,
        "Uses guaranteed-approval or urgency language", _quote(sentence),
    )  # fmt: skip


_OFFICIAL = re.compile(r"\b(official|government)\b", re.I)


def _fake_official_signal(page_text: str, url: str) -> RiskSignal | None:
    sentence = _first_match(_OFFICIAL, None, page_text)
    if not sentence:
        return None
    host = urlparse(url).netloc.lower()
    if host.endswith(".gov") or ".gov." in host:
        return None
    return RiskSignal(
        "fake_official", WEIGHT_FAKE_OFFICIAL,
        f"Calls itself official or government on a non-.gov domain ({host})", _quote(sentence),
    )  # fmt: skip


def _fee_mismatch_signal(doc: dict, fee_signal: RiskSignal | None) -> RiskSignal | None:
    """The page demands a fee, but nothing in our record says this service has one."""
    if fee_signal is None:
        return None
    if doc.get("fees"):
        return None
    return RiskSignal(
        "fee_mismatch", WEIGHT_FEE_MISMATCH,
        "The page asks for a fee, but our record has no fee for this service",
    )  # fmt: skip


# --- weak signals ----------------------------------------------------------------------------


def _domain_mismatch_signal(org_name: str, url: str) -> RiskSignal | None:
    host = urlparse(url).netloc.lower()
    registrable = ".".join(host.split(".")[-2:]) if host.count(".") >= 1 else host
    if registrable in URL_SHORTENERS:
        return RiskSignal(
            "domain_mismatch",
            WEIGHT_DOMAIN_MISMATCH,
            f"Uses a URL shortener ({host}), not its own domain",
        )
    if any(host.endswith(free) for free in FREE_HOSTS):
        return RiskSignal(
            "domain_mismatch",
            WEIGHT_DOMAIN_MISMATCH,
            f"Hosted on a free subdomain ({host}), not its own domain",
        )
    # Real organizations often squash their name into one domain label
    # ("coastsidehope.example"), so check substring containment, not token equality.
    domain_label = re.sub(r"[^a-z0-9]", "", host.split(".")[0])
    org_tokens = _tokens(org_name)
    if org_tokens and domain_label and not any(token in domain_label for token in org_tokens):
        return RiskSignal(
            "domain_mismatch", WEIGHT_DOMAIN_MISMATCH,
            f"The domain ({host}) doesn't resemble the organization's name",
        )  # fmt: skip
    return None


def _new_domain_signal(domain_registered: str | None, today: date) -> RiskSignal | None:
    if not domain_registered:
        return None
    try:
        age = (today - date.fromisoformat(domain_registered[:10])).days
    except ValueError:
        return None
    if age < 0 or age >= NEW_DOMAIN_DAYS:
        return None
    return RiskSignal(
        "new_domain",
        WEIGHT_NEW_DOMAIN,
        f"Website domain registered {age} days ago ({domain_registered})",
    )


def _registry_signal(org_name: str) -> RiskSignal | None:
    if not org_name or in_registry(org_name):
        return None
    message = f"“{org_name}” isn't in the known-organizations registry"
    return RiskSignal("not_in_registry", WEIGHT_NOT_IN_REGISTRY, message)


_MESSAGING_CONTACT = re.compile(r"\b(whatsapp|wa\.me|m\.me|t\.me|telegram)\b", re.I)


def _has_physical_address(doc: dict) -> bool:
    for sal in doc.get("service_at_locations") or []:
        if (sal.get("location") or {}).get("addresses"):
            return True
    return False


def _messaging_only_signal(doc: dict, page_text: str) -> RiskSignal | None:
    if _has_physical_address(doc):
        return None  # any address on file, PO box included, clears this signal
    org = doc.get("organization") or {}
    emails = re.findall(r"[\w.+-]+@([\w.-]+)", page_text)
    free_email_only = bool(emails) and all(e.lower() in FREE_EMAIL_DOMAINS for e in emails)
    messaging_only = bool(_MESSAGING_CONTACT.search(page_text))
    has_phone = bool(doc.get("phones") or org.get("phones"))
    if has_phone or not (free_email_only or messaging_only):
        return None
    return RiskSignal(
        "messaging_only_contact", WEIGHT_MESSAGING_ONLY_CONTACT,
        "Only reachable by messaging app or a free email address, with no physical address on file",
    )  # fmt: skip


def score(signals: list[RiskSignal]) -> int:
    return min(100, sum(s.weight for s in signals))


def is_marked_safe(session, service_id: str, url: str) -> bool:
    """True once a specialist has marked this listing, or this URL's domain, safe
    (maintenance/review.py's ``mark_safe``) -- checked before a new suspicious finding is
    created, so marking safe actually stops the next check from re-flagging it."""
    from sqlalchemy import select

    from shared.models import Finding

    domain = urlparse(url).netloc.lower()
    rows = session.execute(
        select(Finding.service_id, Finding.evidence_url).where(
            Finding.category == "suspicious", Finding.review_state == "marked_safe"
        )
    ).all()
    return any(sid == service_id or urlparse(u).netloc.lower() == domain for sid, u in rows)


def assess(
    doc: dict,
    page_text: str,
    url: str,
    domain_registered: str | None,
    today: date | None = None,
) -> list[RiskSignal]:
    """Every signal the page and record trip, in a stable order. Pure and deterministic --
    no network, no model call (that's ``maintenance.suspicious_bedrock``, additive only)."""
    today = today or date.today()
    org = doc.get("organization") or {}
    org_name = org.get("name") or ""
    signals: list[RiskSignal] = []

    fee = find_upfront_fee(page_text)
    fee_signal = None
    if fee and quote_on_page(fee.quote, page_text):
        amount = f" {fee.amount}" if fee.amount else ""
        fee_signal = RiskSignal(
            "fee_before_help", WEIGHT_FEE_BEFORE_HELP,
            f"Asks for a{amount} fee before helping", _quote(fee.quote),
        )  # fmt: skip
        signals.append(fee_signal)

    if signal := _sensitive_data_signal(page_text):
        signals.append(signal)
    if signal := _urgency_signal(page_text):
        signals.append(signal)
    if signal := _fake_official_signal(page_text, url):
        signals.append(signal)
    if signal := _fee_mismatch_signal(doc, fee_signal):
        signals.append(signal)
    if signal := _domain_mismatch_signal(org_name, url):
        signals.append(signal)
    if signal := _new_domain_signal(domain_registered, today):
        signals.append(signal)
    if signal := _registry_signal(org_name):
        signals.append(signal)
    if signal := _messaging_only_signal(doc, page_text):
        signals.append(signal)

    return signals


def suspicious_finding(
    service_id: str,
    doc: dict,
    url: str,
    signals: list[RiskSignal],
    model_id: str | None,
    extra_reasons: list[str] | None = None,
) -> FindingDraft | None:
    """A finding once the score reaches THRESHOLD, else None (signals alone aren't saved as
    a finding; the check record still carries them -- see maintenance/worker/run_check.py)."""
    total = score(signals)
    if total < THRESHOLD:
        return None
    quoted = next((s for s in signals if s.quote), None)
    reasons = [s.message for s in signals] + list(extra_reasons or [])
    return FindingDraft(
        category="suspicious",
        field="listing",
        entity_type="service",
        entity_id=service_id,
        entity_field="status",
        evidence_url=url,
        current_value=doc.get("status"),
        proposed_value=None,
        evidence_quote=quoted.quote if quoted else signals[0].message,
        confidence=min(0.95, 0.5 + total / 200),
        model_id=model_id,
        risk_signals=reasons,
        risk_score=total,
    )
