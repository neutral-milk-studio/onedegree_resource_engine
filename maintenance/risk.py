"""Scam risk signals, checked inside the same website check (no extra model call).

Signals:
- ``fee_before_help``: the page asks for money before helping.
- ``new_domain``: the website's domain was registered recently (default: < 180 days).
- ``name_mismatch``: the name the site uses doesn't match the listing's organization.
- ``not_in_registry``: the organization isn't in the nonprofit registry.

Two or more signals -> a ``suspicious`` finding, always worded "flagged for review", never
"proven scam". One signal alone is recorded on the check but doesn't flag the record.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

from maintenance.comparator.compare import FindingDraft
from maintenance.extractor.extract import PageFacts
from maintenance.extractor.parse import quote_on_page

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "data" / "registry" / "nonprofit_registry.csv"
NEW_DOMAIN_DAYS = int(os.environ.get("RE_NEW_DOMAIN_DAYS", "180"))
SUSPICIOUS_AT = 2
_STOP = {"the", "of", "and", "inc", "incorporated", "llc", "co", "center", "centre", "for", "a"}


@dataclass(frozen=True)
class RiskSignal:
    code: str
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
def _registry(path: str) -> tuple[frozenset[frozenset[str]], frozenset[str]]:
    names, eins = set(), set()
    with open(path, encoding="utf-8") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        for row in rows:
            names.add(frozenset(_tokens(row["name"])))
            if row.get("ein"):
                eins.add(re.sub(r"\D", "", row["ein"]))
    return frozenset(names), frozenset(eins)


def in_registry(org_name: str, tax_id: str | None = None) -> bool:
    names, eins = _registry(os.environ.get("RE_REGISTRY_FILE", str(REGISTRY)))
    if tax_id and re.sub(r"\D", "", tax_id) in eins:
        return True
    return frozenset(_tokens(org_name)) in names


def assess(
    doc: dict,
    facts: PageFacts | None,
    page_text: str,
    domain_registered: str | None,
    today: date | None = None,
) -> list[RiskSignal]:
    today = today or date.today()
    org = doc.get("organization") or {}
    org_name = org.get("name") or ""
    signals: list[RiskSignal] = []

    fee = facts.fee_before_help if facts else None
    if fee and fee.required and quote_on_page(fee.quote, page_text):
        amount = f" {fee.amount}" if fee.amount else ""
        signals.append(
            RiskSignal("fee_before_help", f"Asks for a{amount} fee before helping", fee.quote)
        )

    if domain_registered:
        try:
            age = (today - date.fromisoformat(domain_registered[:10])).days
        except ValueError:
            age = None
        if age is not None and age < NEW_DOMAIN_DAYS:
            signals.append(
                RiskSignal(
                    "new_domain", f"Website domain registered {age} days ago ({domain_registered})"
                )
            )

    site_name = facts.organization_name if facts else None
    if (
        site_name
        and quote_on_page(site_name.quote, page_text)
        and not names_match(site_name.text, org_name)
    ):
        signals.append(
            RiskSignal(
                "name_mismatch",
                f"The website calls itself “{site_name.text}”, but the listing says “{org_name}”",
                site_name.quote,
            )
        )

    if org_name and not in_registry(org_name, org.get("tax_id")):
        signals.append(
            RiskSignal("not_in_registry", f"“{org_name}” isn't in the nonprofit registry")
        )
    return signals


def suspicious_finding(
    service_id: str, doc: dict, url: str, signals: list[RiskSignal], model_id: str | None
) -> FindingDraft | None:
    if len(signals) < SUSPICIOUS_AT:
        return None
    quoted = next((s for s in signals if s.quote), None)
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
        confidence=min(0.95, 0.5 + 0.15 * len(signals)),
        model_id=model_id,
        risk_signals=[s.message for s in signals],
    )
